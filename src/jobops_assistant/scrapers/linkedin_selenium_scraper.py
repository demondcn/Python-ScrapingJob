from __future__ import annotations

from datetime import UTC, datetime
from time import sleep
from urllib.parse import quote, urljoin

from bs4 import Tag

from .base_scraper import CaptchaRequiredError, LoginRequiredError, ScrapedJob, SourceBlockedError
from .selenium_base import SeleniumJobScraper


LINKEDIN_BLOCKED_MESSAGE = "LinkedIn pidió login/captcha/authwall. Se omite esta fuente."
LINKEDIN_SIGNIN_MODAL_CLOSED_MESSAGE = "LinkedIn: modal de inicio de sesión cerrado"
LINKEDIN_SIGNIN_MODAL_NOT_FOUND_MESSAGE = "LinkedIn: no se encontró modal de inicio de sesión"
LINKEDIN_PUBLIC_CARDS_MESSAGE = "LinkedIn: cards públicas detectadas"
LINKEDIN_LOGGED_CARDS_MESSAGE = "LinkedIn: cards logueadas detectadas"
LINKEDIN_LOGGED_EXTRACTION_MESSAGE = "LinkedIn: extracción en modo logueado"
LINKEDIN_AUTHWALL_WITHOUT_CARDS_MESSAGE = "LinkedIn: authwall/login detectado sin cards"
LINKEDIN_EMPTY_RESULTS_MESSAGE = "LinkedIn: no hay resultados"

LINKEDIN_PUBLIC_CARD_SELECTORS = (
    "div.base-card",
    "div.base-search-card",
    "div.job-search-card",
    "ul.jobs-search__results-list li",
    "a.base-card__full-link",
    "h3.base-search-card__title",
)
LINKEDIN_LOGGED_CARD_SELECTORS = (
    "li.jobs-search-results__list-item",
    "div.job-card-container",
    "div.job-card-list",
    "a.job-card-container__link",
    "a.job-card-list__title",
)

DATE_POSTED_FILTERS = {
    "24h": "r86400",
    "week": "r604800",
    "month": "r2592000",
}
EXPERIENCE_LEVEL_FILTERS = {
    "internship": "1",
    "entry_level": "2",
    "associate": "3",
    "1": "1",
    "2": "2",
    "3": "3",
}
WORKPLACE_TYPE_FILTERS = {
    "onsite": "1",
    "remote": "2",
    "hybrid": "3",
    "1": "1",
    "2": "2",
    "3": "3",
}


def close_linkedin_signin_modal(driver, *, attempts: int = 2) -> bool:
    """Close LinkedIn's public sign-in prompt without interacting with login actions."""

    attempts = max(1, min(attempts, 3))
    for attempt in range(attempts):
        for button in _find_linkedin_signin_dismiss_buttons(driver):
            if not _is_safe_linkedin_dismiss_button(button):
                continue
            try:
                button.click()
            except Exception:
                continue
            print(LINKEDIN_SIGNIN_MODAL_CLOSED_MESSAGE)
            sleep(1)
            return True
        if attempt < attempts - 1:
            sleep(0.25)

    print(LINKEDIN_SIGNIN_MODAL_NOT_FOUND_MESSAGE)
    return False


def _find_linkedin_signin_dismiss_buttons(driver) -> list:
    try:
        from selenium.webdriver.common.by import By
        css_selector = By.CSS_SELECTOR
    except ImportError:
        css_selector = "css selector"

    selectors = (
        "button[aria-label*='Dismiss']",
        "button[aria-label*='dismiss']",
        "button[aria-label*='Cerrar']",
        "button[aria-label*='cerrar']",
        "button[aria-label*='Close']",
        "button[aria-label*='close']",
        "button.modal__dismiss",
        "button[class*='modal__dismiss']",
        "button[class*='modal-dismiss']",
        "button[class*='dismiss']",
        "button",
    )
    buttons = []
    seen_ids: set[int] = set()
    for selector in selectors:
        try:
            matches = driver.find_elements(css_selector, selector)
        except Exception:
            continue
        for match in matches:
            match_id = id(match)
            if match_id in seen_ids:
                continue
            seen_ids.add(match_id)
            buttons.append(match)
    return buttons


def _is_safe_linkedin_dismiss_button(button) -> bool:
    try:
        if hasattr(button, "is_displayed") and not button.is_displayed():
            return False
        if hasattr(button, "is_enabled") and not button.is_enabled():
            return False
    except Exception:
        return False

    label = _safe_element_attribute(button, "aria-label")
    class_name = _safe_element_attribute(button, "class")
    text = _safe_element_text(button)
    combined = f"{label} {class_name} {text}".casefold()
    unsafe_tokens = (
        "continuar con google",
        "continue with google",
        "iniciar sesión con el email",
        "iniciar sesion con el email",
        "sign in with email",
        "unirse ahora",
        "join now",
        "solicitar",
        "apply",
        "guardar",
        "save",
    )
    if any(token in combined for token in unsafe_tokens):
        return False

    if text.strip() in {"×", "x", "X"}:
        return True
    safe_tokens = ("dismiss", "cerrar", "close", "modal__dismiss", "modal-dismiss")
    return any(token in combined for token in safe_tokens)


def _safe_element_attribute(element, name: str) -> str:
    try:
        return str(element.get_attribute(name) or "")
    except Exception:
        return ""


def _safe_element_text(element) -> str:
    try:
        return str(getattr(element, "text", "") or "")
    except Exception:
        return ""


def has_linkedin_job_cards(soup) -> bool:
    return has_public_linkedin_job_cards(soup) or has_logged_in_linkedin_job_cards(soup)


def has_public_linkedin_job_cards(soup) -> bool:
    return any(soup.select_one(selector) for selector in LINKEDIN_PUBLIC_CARD_SELECTORS)


def has_logged_in_linkedin_job_cards(soup) -> bool:
    if any(soup.select_one(selector) for selector in LINKEDIN_LOGGED_CARD_SELECTORS):
        return True
    list_container = soup.select_one("div.scaffold-layout__list-container")
    if not list_container:
        return False
    item_selectors = (
        "li",
        "div.job-card-container",
        "div.job-card-list",
        "a[href*='/jobs/view/']",
    )
    return any(list_container.select_one(selector) for selector in item_selectors)


def build_linkedin_jobs_url(
    keyword: str,
    location: str,
    date_posted: str = "24h",
    experience_levels: list[str] | tuple[str, ...] | str | None = None,
    workplace_types: list[str] | tuple[str, ...] | str | None = None,
) -> str:
    keyword = (keyword or "").strip()
    location = (location or "").strip()
    if not keyword:
        raise ValueError("Debes indicar --keyword para construir la URL de LinkedIn.")
    if not location:
        raise ValueError("Debes indicar --location para construir la URL de LinkedIn.")

    params = [
        ("keywords", keyword),
        ("location", location),
    ]

    normalized_date = (date_posted or "").strip().casefold()
    if normalized_date and normalized_date != "any":
        date_code = DATE_POSTED_FILTERS.get(normalized_date)
        if date_code is None:
            supported = ", ".join(sorted([*DATE_POSTED_FILTERS, "any"]))
            raise ValueError(f"date_posted no soportado: {date_posted}. Usa: {supported}.")
        params.append(("f_TPR", date_code))

    experience_codes = _map_filter_values(
        experience_levels,
        EXPERIENCE_LEVEL_FILTERS,
        "experience_levels",
    )
    if experience_codes:
        params.append(("f_E", ",".join(experience_codes)))

    workplace_codes = _map_filter_values(
        workplace_types,
        WORKPLACE_TYPE_FILTERS,
        "workplace_types",
    )
    if workplace_codes:
        params.append(("f_WT", ",".join(workplace_codes)))

    query = "&".join(f"{quote(key)}={quote(value, safe=',')}" for key, value in params)
    return f"https://www.linkedin.com/jobs/search/?{query}"


def _map_filter_values(
    values: list[str] | tuple[str, ...] | str | None,
    mapping: dict[str, str],
    label: str,
) -> list[str]:
    if values is None:
        return []
    raw_values = [values] if isinstance(values, str) else list(values)
    mapped_values: list[str] = []
    for raw_value in raw_values:
        normalized = (raw_value or "").strip().casefold()
        if not normalized:
            continue
        mapped = mapping.get(normalized)
        if mapped is None:
            supported = ", ".join(sorted(key for key in mapping if not key.isdigit()))
            raise ValueError(f"{label} no soportado: {raw_value}. Usa: {supported}.")
        if mapped not in mapped_values:
            mapped_values.append(mapped)
    return mapped_values


class LinkedInSeleniumJobScraper(SeleniumJobScraper):
    portal_name = "linkedin_selenium"
    blocked_error_message = LINKEDIN_BLOCKED_MESSAGE
    captcha_error_message = LINKEDIN_BLOCKED_MESSAGE
    login_error_message = LINKEDIN_BLOCKED_MESSAGE

    card_selectors = (
        "div.base-card",
        "div.base-search-card",
        "div.job-search-card",
        "li.jobs-search-results__list-item",
        "div.job-card-container",
        "div.job-card-list",
        "ul.jobs-search__results-list li",
        "div.scaffold-layout__list-container li",
    )
    title_selectors = (
        "h3.base-search-card__title",
        ".base-search-card__title",
        "a.base-card__full-link span.sr-only",
        "a.job-card-list__title strong",
        "a.job-card-list__title",
        "a.job-card-container__link strong",
        "a.job-card-container__link",
        ".job-card-list__title strong",
        ".job-card-list__title",
        ".job-card-container__title",
        ".artdeco-entity-lockup__title strong",
        ".artdeco-entity-lockup__title",
        "a.base-card__full-link",
        "strong",
        "h3",
    )
    company_selectors = (
        "h4.base-search-card__subtitle",
        ".base-search-card__subtitle",
        ".job-search-card__subtitle",
        "span.job-card-container__primary-description",
        ".job-card-container__primary-description",
        "div.artdeco-entity-lockup__subtitle",
        ".artdeco-entity-lockup__subtitle",
        ".job-card-container__company-name",
        ".job-card-container__subtitle",
        "h4",
    )
    location_selectors = (
        "span.job-search-card__location",
        ".job-search-card__location",
        "li.job-card-container__metadata-item",
        ".job-card-container__metadata-item",
        ".job-card-container__metadata-wrapper li",
        ".artdeco-entity-lockup__caption",
        ".base-search-card__metadata",
        ".job-search-card__metadata",
        ".job-card-container__metadata-wrapper",
    )
    link_selectors = (
        "a.base-card__full-link[href]",
        "a.job-card-container__link[href]",
        "a.job-card-list__title[href]",
        "a[href*='/jobs/view/'][href]",
    )
    posted_selectors = (
        "time.job-search-card__listdate",
        "time.job-search-card__listdate--new",
        "time",
    )
    description_selectors = (
        "div.description__text",
        ".job-search-card__snippet",
        ".job-posting-benefits__text",
        ".job-card-container__job-insight-text",
        ".job-card-container__footer-item",
        ".job-card-list__insight",
        ".base-search-card__metadata",
    )

    def parse_search_results(self, html: str, source) -> list[ScrapedJob]:
        soup = self._soup(html)
        if has_logged_in_linkedin_job_cards(soup):
            print(LINKEDIN_LOGGED_EXTRACTION_MESSAGE)
        results: list[ScrapedJob] = []
        seen_urls: set[str] = set()
        for card in self._select_cards(soup):
            url = self._first_attr(card, self.link_selectors, "href")
            title = self._first_text(card, self.title_selectors)
            if not url or not title:
                continue

            absolute_url = self._absolute_linkedin_url(source, url)
            normalized_url = self.normalize_url(absolute_url)
            if not normalized_url or normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)

            company = self._first_text(card, self.company_selectors)
            location = self._first_text(card, self.location_selectors)
            raw_posted_text = self._first_text(card, self.posted_selectors)
            description = self._extract_card_description(card, location, raw_posted_text)
            results.append(
                ScrapedJob(
                    title=title,
                    company=company,
                    portal=self.portal_name,
                    location=location,
                    modality=self._infer_modality(location, description),
                    salary="",
                    url=normalized_url,
                    description=description,
                    requirements="",
                    published_at=self._parse_published_at(raw_posted_text),
                    found_at=datetime.now(UTC),
                    raw_posted_text=raw_posted_text,
                    source_id=source.id,
                )
            )
        visible_detail = self._extract_detail_description(html)
        if visible_detail and results and not results[0].description:
            results[0].description = visible_detail
            results[0].modality = self._infer_modality(results[0].location, visible_detail)
        return results

    def fetch_job_detail(self, job: ScrapedJob, source) -> ScrapedJob:
        if not getattr(self.settings, "linkedin_fetch_details", False):
            return job

        driver = self._build_driver()
        try:
            driver.set_page_load_timeout(self.settings.selenium_page_load_timeout)
            self._navigate_to_url(driver, job.url)
            pause = max(0, self.settings.selenium_scroll_pause)
            if pause:
                sleep(pause)
            html = getattr(driver, "page_source", "") or ""
            current_url = getattr(driver, "current_url", "") or job.url
            description = self._extract_detail_description(html)
            reason, _kind = self._detect_linkedin_block_reason(
                html,
                has_public_content=bool(description),
                require_public_content=False,
                current_url=current_url,
            )
            if reason:
                return job
            if description:
                job.description = description
        except Exception:
            return job
        finally:
            try:
                driver.quit()
            except Exception:
                pass
        return job

    def normalize_url(self, url: str) -> str:
        normalized = super().normalize_url(url)
        return normalized.replace("http://www.linkedin.com/", "https://www.linkedin.com/")

    def _absolute_linkedin_url(self, source, url: str) -> str:
        if url.startswith("/jobs/view/"):
            return f"https://www.linkedin.com{url}"
        return urljoin(source.search_url, url)

    def has_public_job_content(self, html: str) -> bool:
        return has_linkedin_job_cards(self._soup(html))

    def has_empty_results_content(self, html: str) -> bool:
        soup = self._soup(html)
        visible_text = self._normalize_text(soup.get_text(" ", strip=True))
        empty_result_signals = (
            "no se han encontrado coincidencias",
            "no se encontraron coincidencias",
            "no hay resultados",
            "no results found",
            "no matching jobs found",
            "comprueba que las palabras clave estén bien escritas",
            "comprueba que las palabras clave esten bien escritas",
        )
        return any(signal in visible_text for signal in empty_result_signals)

    def _scroll_page(self, driver) -> None:
        pause = max(0, self.settings.selenium_scroll_pause)
        max_scrolls = max(0, self.settings.selenium_max_scrolls)
        if pause:
            sleep(pause)
        close_linkedin_signin_modal(driver)
        for _ in range(max_scrolls):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            self._click_show_more_button(driver)
            if pause:
                sleep(pause)

    def _detect_blocked_content(self, html: str) -> None:
        soup = self._soup(html)
        has_public_cards = has_public_linkedin_job_cards(soup)
        has_logged_cards = has_logged_in_linkedin_job_cards(soup)
        has_cards = has_public_cards or has_logged_cards
        if has_public_cards:
            print(LINKEDIN_PUBLIC_CARDS_MESSAGE)
            return
        if has_logged_cards:
            print(LINKEDIN_LOGGED_CARDS_MESSAGE)
            return
        if self.has_empty_results_content(html):
            print(LINKEDIN_EMPTY_RESULTS_MESSAGE)
            return
        current_url = ""
        if self.last_response_debug is not None:
            current_url = self.last_response_debug.final_url
        reason, kind = self._detect_linkedin_block_reason(
            html,
            has_public_content=has_cards,
            require_public_content=True,
            current_url=current_url,
        )
        if not reason:
            return
        self._set_block_reason(reason)
        if kind in {"login", "blocked"}:
            print(LINKEDIN_AUTHWALL_WITHOUT_CARDS_MESSAGE)
        if kind == "captcha":
            raise CaptchaRequiredError(self.captcha_error_message)
        if kind == "login":
            raise LoginRequiredError(self.login_error_message)
        raise SourceBlockedError(self.blocked_error_message)

    def _extract_card_description(self, card: Tag, location: str, raw_posted_text: str) -> str:
        description = self._first_text(card, self.description_selectors)
        if description in {location, raw_posted_text}:
            return ""
        return description[:500]

    def _extract_detail_description(self, html: str) -> str:
        soup = self._soup(html)
        description = soup.select_one("div.description__text")
        if not description:
            return ""
        return self._clean_text(description.get_text(" ", strip=True))

    def _detect_linkedin_block_reason(
        self,
        html: str,
        *,
        has_public_content: bool,
        require_public_content: bool,
        current_url: str = "",
    ) -> tuple[str, str]:
        soup = self._soup(html)
        visible_text = self._normalize_text(soup.get_text(" ", strip=True))
        html_text = self._normalize_text(html)
        url_text = self._normalize_text(current_url)
        combined = f"{visible_text} {html_text} {url_text}"

        blocking_signals = {
            "captcha": ("captcha", "captcha"),
            "recaptcha": ("recaptcha", "captcha"),
            "security verification": ("security verification", "captcha"),
            "security check": ("security check", "captcha"),
            "verify you are human": ("captcha", "captcha"),
            "checkpoint": ("checkpoint", "login"),
        }
        for token, result in blocking_signals.items():
            if token in combined:
                return result

        login_wall_signals = {
            "authwall": ("authwall", "login"),
            "youre almost there": ("authwall", "login"),
            "you're almost there": ("authwall", "login"),
            "you’re almost there": ("authwall", "login"),
        }
        for token, result in login_wall_signals.items():
            if not has_public_content and token in combined:
                return result

        blocking_selectors = {
            "form[action*='checkpoint']": ("checkpoint", "login"),
            "form[action*='captcha']": ("captcha", "captcha"),
            "iframe[src*='captcha']": ("captcha", "captcha"),
            "iframe[src*='recaptcha']": ("recaptcha", "captcha"),
            ".g-recaptcha": ("captcha", "captcha"),
        }
        for selector, result in blocking_selectors.items():
            if soup.select_one(selector):
                return result

        login_wall_selectors = {
            ".authwall": ("authwall", "login"),
            "[class*='authwall']": ("authwall", "login"),
        }
        for selector, result in login_wall_selectors.items():
            if not has_public_content and soup.select_one(selector):
                return result

        soft_login_signals = (
            "login",
            "sign in",
            "join linkedin",
            "inicia sesion",
            "inicia sesión",
            "iniciar sesion",
            "iniciar sesión",
            "unirte a linkedin",
        )
        if not has_public_content:
            for token in soft_login_signals:
                if token in combined:
                    return "login requerido", "login"

        if require_public_content and not has_public_content:
            return "sin cards publicas de LinkedIn", "blocked"
        return "", ""

    def _click_show_more_button(self, driver) -> None:
        try:
            from selenium.webdriver.common.by import By
        except ImportError:
            return

        try:
            buttons = driver.find_elements(By.CSS_SELECTOR, ".infinite-scroller__show-more-button")
        except Exception:
            return

        for button in buttons:
            try:
                if hasattr(button, "is_displayed") and not button.is_displayed():
                    continue
                if hasattr(button, "is_enabled") and not button.is_enabled():
                    continue
                button.click()
                return
            except Exception:
                continue
