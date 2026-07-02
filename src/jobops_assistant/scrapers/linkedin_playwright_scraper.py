from __future__ import annotations

from datetime import UTC, datetime
from time import sleep
from urllib.parse import urljoin

from bs4 import Tag

from ..application_types import EXTERNAL_APPLY, LINKEDIN_EASY_APPLY, UNKNOWN_APPLICATION_TYPE
from .base_scraper import CaptchaRequiredError, LoginRequiredError, ScrapedJob, SourceBlockedError
from .linkedin_selenium_scraper import (
    LINKEDIN_AUTHWALL_WITHOUT_CARDS_MESSAGE,
    LINKEDIN_BLOCKED_MESSAGE,
    LINKEDIN_EMPTY_RESULTS_MESSAGE,
    LINKEDIN_EXTERNAL_APPLY_SIGNALS,
    LINKEDIN_GENERIC_APPLY_CONTROL_TEXTS,
    LINKEDIN_LOGGED_CARDS_MESSAGE,
    LINKEDIN_LOGGED_EXTRACTION_MESSAGE,
    LINKEDIN_PUBLIC_CARDS_MESSAGE,
    _collect_application_text,
    _collect_control_text,
    _has_easy_apply_signal,
    _has_external_apply_signal,
    _normalize_application_text,
    has_linkedin_job_cards,
    has_logged_in_linkedin_job_cards,
    has_public_linkedin_job_cards,
)
from .playwright_base import PlaywrightJobScraper

LINKEDIN_PLAYWRIGHT_MANUAL_LOGIN_MESSAGE = (
    "LinkedIn Playwright: inicia sesion manualmente en la ventana abierta y presiona ENTER para continuar."
)


class LinkedInPlaywrightJobScraper(PlaywrightJobScraper):
    portal_name = "linkedin_playwright"
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
    ready_selectors = (
        *card_selectors,
        "div.global-nav",
        "body",
    )

    def __init__(self, settings, driver_factory=None, *, log_playwright: bool = True) -> None:
        super().__init__(settings, driver_factory=driver_factory, log_playwright=log_playwright)
        self.session_active = False
        self.session_mode = "public"

    def _is_ultra_fast_mode_enabled(self) -> bool:
        return bool(getattr(self.settings, "playwright_ultra_fast_mode", False))

    def _get_effective_page_load_timeout(self) -> int:
        timeout = super()._get_effective_page_load_timeout()
        if self._is_ultra_fast_mode_enabled():
            return max(1, min(20, timeout))
        if not self._is_fast_mode_enabled():
            return timeout
        return max(1, min(15, int(timeout * 0.5) or 1))

    def _get_playwright_retry_attempts(self) -> int:
        return 1

    def _get_linkedin_settle_seconds(self) -> float:
        if self._is_ultra_fast_mode_enabled():
            return 0.0
        if self._is_fast_mode_enabled():
            return 0.4
        return min(0.5, max(0.3, float(getattr(self.settings, "playwright_scroll_pause", 0) or 0.3)))

    def _wait_for_rendered_dom(self, driver, selectors: tuple[str, ...]) -> None:
        timeout = self._get_effective_page_load_timeout()
        wait_for_load_state = getattr(driver, "wait_for_load_state", None)
        if callable(wait_for_load_state):
            wait_for_load_state("domcontentloaded", timeout=timeout)
        self._wait_for_timeout(driver, self._get_linkedin_settle_seconds())

    def _scroll_page(self, driver) -> None:
        if self._is_ultra_fast_mode_enabled():
            return
        self._click_show_more_button(driver)
        driver.execute_script("window.scrollBy(0, Math.max(700, Math.floor(window.innerHeight * 0.9)));")
        self._wait_for_timeout(driver, 0.3 if self._is_fast_mode_enabled() else 0.5)

    def _wait_for_timeout(self, driver, seconds: float) -> None:
        wait_for_timeout = getattr(driver, "wait_for_timeout", None)
        if seconds <= 0:
            return
        if callable(wait_for_timeout):
            wait_for_timeout(seconds)
            return
        sleep(seconds)

    def _driver_has_linkedin_cards(self, driver) -> bool:
        find_elements = getattr(driver, "find_elements", None)
        if not callable(find_elements):
            return False
        for selector in self.card_selectors:
            try:
                if find_elements(None, selector):
                    return True
            except Exception:
                continue
        return False

    def _load_rendered_html(self, driver, requested_url: str, selectors: tuple[str, ...]) -> tuple[str, str, int | None]:
        driver.set_page_load_timeout(self._get_effective_page_load_timeout())
        try:
            self._navigate_to_url(driver, requested_url)
            self._wait_for_rendered_dom(driver, selectors)
            if not self._driver_has_linkedin_cards(driver):
                self._scroll_page(driver)
            html = getattr(driver, "page_source", "") or ""
            final_url = getattr(driver, "current_url", "") or requested_url
            self._log_playwright(f"Playwright: html_length={len(html)}")
            return html, final_url, 200 if html else None
        except Exception as exc:
            current_html = getattr(driver, "page_source", "") or ""
            current_url = getattr(driver, "current_url", "") or requested_url
            if current_html and self.has_public_job_content(current_html):
                self._log_playwright(f"Playwright: carga parcial reutilizada tras error en {requested_url}")
                return current_html, current_url, 200
            raise RuntimeError(str(exc) if exc is not None else "playwright load failed")

    def fetch_search_results(self, source) -> str:
        if not getattr(self.settings, "enable_playwright", False):
            return super().fetch_search_results(source)

        driver = self._build_driver()
        requested_url = self.build_search_url(source)
        interactive_login = bool(getattr(source, "interactive_login", False))
        try:
            html, final_url, status_code = self._load_rendered_html(driver, requested_url, self.ready_selectors)
            if interactive_login and self._needs_manual_login(html, final_url):
                self._navigate_to_url(driver, "https://www.linkedin.com/login")
                print(LINKEDIN_PLAYWRIGHT_MANUAL_LOGIN_MESSAGE)
                input()
                html, final_url, status_code = self._load_rendered_html(driver, requested_url, self.ready_selectors)

            self.last_response_debug = self._build_debug_snapshot(requested_url, final_url, html, status_code)
            self.session_active = self._is_logged_in_session(html, final_url)
            self.session_mode = "logged_in" if self.session_active else "public"
            self._detect_blocked_content(html)
            return html
        finally:
            try:
                driver.quit()
            except Exception:
                pass

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
            application_type = self._detect_application_type_from_node(card)
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
                    application_type=application_type,
                )
            )
        visible_detail = self._extract_detail_description(html)
        if visible_detail and results and not results[0].description:
            results[0].description = visible_detail
            results[0].modality = self._infer_modality(results[0].location, visible_detail)
        visible_detail_application_type = self._detect_application_type_from_detail_panel(html)
        if (
            visible_detail_application_type != UNKNOWN_APPLICATION_TYPE
            and results
            and results[0].application_type == UNKNOWN_APPLICATION_TYPE
        ):
            results[0].application_type = visible_detail_application_type
        return results

    def fetch_job_detail(self, job: ScrapedJob, source) -> ScrapedJob:
        if not getattr(self.settings, "linkedin_fetch_details", False):
            return job

        driver = self._build_driver()
        try:
            driver.set_page_load_timeout(self._get_effective_page_load_timeout())
            self._navigate_to_url(driver, job.url)
            self._wait_for_rendered_dom(driver, ("div.description__text", "body"))
            html = getattr(driver, "page_source", "") or ""
            current_url = getattr(driver, "current_url", "") or job.url
            description = self._extract_detail_description(html)
            application_type = self._detect_application_type_from_html(html)
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
            if application_type != UNKNOWN_APPLICATION_TYPE:
                job.application_type = application_type
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
            "comprueba que las palabras clave esten bien escritas",
            "comprueba que las palabras clave estén bien escritas",
        )
        return any(signal in visible_text for signal in empty_result_signals)

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

    def _needs_manual_login(self, html: str, current_url: str) -> bool:
        soup = self._soup(html)
        has_cards = has_linkedin_job_cards(soup)
        reason, kind = self._detect_linkedin_block_reason(
            html,
            has_public_content=has_cards,
            require_public_content=False,
            current_url=current_url,
        )
        return kind == "login" and not has_cards and not has_public_linkedin_job_cards(soup)

    def _is_logged_in_session(self, html: str, current_url: str) -> bool:
        soup = self._soup(html)
        if has_logged_in_linkedin_job_cards(soup):
            return True
        current_url_normalized = self._normalize_text(current_url)
        if "/login" in current_url_normalized or "authwall" in current_url_normalized or "checkpoint" in current_url_normalized:
            return False
        visible_text = self._normalize_text(soup.get_text(" ", strip=True))
        nav_signals = (
            "mi red empleos mensajes notificaciones yo",
            "my network jobs messaging notifications me",
        )
        return any(signal in visible_text for signal in nav_signals) or bool(soup.select_one("nav.global-nav, .global-nav"))

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

    def _detect_application_type_from_html(self, html: str) -> str:
        return self._detect_application_type_from_node(self._soup(html))

    def _detect_application_type_from_detail_panel(self, html: str) -> str:
        soup = self._soup(html)
        for selector in (
            "div.jobs-search__job-details--container",
            "div.jobs-details",
            "div.jobs-details__main-content",
            "div.job-details-jobs-unified-top-card",
            "div.jobs-unified-top-card",
            "section.top-card-layout",
            "div.top-card-layout",
        ):
            panel = soup.select_one(selector)
            if isinstance(panel, Tag):
                application_type = self._detect_application_type_from_node(panel)
                if application_type != UNKNOWN_APPLICATION_TYPE:
                    return application_type
        return UNKNOWN_APPLICATION_TYPE

    def _detect_application_type_from_node(self, node: Tag) -> str:
        text = _normalize_application_text(_collect_application_text(node))
        if _has_easy_apply_signal(text):
            return LINKEDIN_EASY_APPLY
        if _has_external_apply_signal(text) or self._has_generic_external_apply_control(node):
            return EXTERNAL_APPLY
        return UNKNOWN_APPLICATION_TYPE

    def _has_generic_external_apply_control(self, node: Tag) -> bool:
        controls = node.select("button, a, [role='button']")
        for control in controls:
            control_text = _normalize_application_text(_collect_control_text(control))
            if not control_text or _has_easy_apply_signal(control_text):
                continue
            if _has_external_apply_signal(control_text):
                return True
            if control_text in LINKEDIN_GENERIC_APPLY_CONTROL_TEXTS:
                return True
            if control_text.startswith("solicitar ") or control_text.startswith("apply "):
                return True
        return False

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
        find_elements = getattr(driver, "find_elements", None)
        if not callable(find_elements):
            return
        try:
            buttons = find_elements(None, ".infinite-scroller__show-more-button")
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

    def _build_debug_snapshot(self, requested_url: str, final_url: str, html: str, status_code: int | None):
        from .base_scraper import ResponseDebugSnapshot

        return ResponseDebugSnapshot(
            requested_url=requested_url,
            status_code=status_code,
            final_url=self._clean_text(final_url),
            content_type="text/html",
            html=html,
        )
