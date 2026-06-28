from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

from bs4 import BeautifulSoup, Tag

from .base_scraper import CaptchaRequiredError, LoginRequiredError, ResponseDebugSnapshot, ScrapedJob, SourceBlockedError
from .computrabajo_scraper import (
    COMPUTRABAJO_STATE_SCRAPER_BROKEN,
    ComputrabajoJobScraper,
    _normalize_computrabajo_text,
)
from .playwright_base import PLAYWRIGHT_INSTALL_MESSAGE, PlaywrightJobScraper

logger = logging.getLogger(__name__)

COMPUTRABAJO_PLAYWRIGHT_MODE = "simple"
COMPUTRABAJO_SIMPLE_WAIT_SECONDS = 4.0
COMPUTRABAJO_SIMPLE_CARD_SELECTORS = (
    ".box_offer",
    ".js-card",
    "article",
)
COMPUTRABAJO_SIMPLE_LINK_SELECTORS = (
    "a[href*='/oferta/']",
    "a[href*='/ofertas/']",
    "a[href*='/oferta-de-trabajo']",
)


class _RawComputrabajoPlaywrightDriver:
    def __init__(self, timeout_seconds: int) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - exercised via friendly error path.
            raise SourceBlockedError(PLAYWRIGHT_INSTALL_MESSAGE) from exc

        self.timeout_seconds = max(1, int(timeout_seconds or 1))
        self.timeout_ms = self.timeout_seconds * 1000
        self.current_url = ""
        self.quit_called = False
        self._page_source = ""
        self.last_status_code: int | None = None
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=False)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()

    @property
    def page_source(self) -> str:
        return self._page_source

    def set_page_load_timeout(self, timeout: int) -> None:
        self.timeout_seconds = max(1, int(timeout or 1))
        self.timeout_ms = self.timeout_seconds * 1000
        try:
            self._page.set_default_timeout(self.timeout_ms)
            self._page.set_default_navigation_timeout(self.timeout_ms)
        except Exception:
            pass

    def get(self, url: str) -> None:
        response = self._page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        self.current_url = str(getattr(self._page, "url", "") or url)
        self.last_status_code = getattr(response, "status", None) if response is not None else None
        self._page_source = str(self._page.content() or "")

    def wait_for_timeout(self, seconds: float) -> None:
        delay_ms = max(0, int(float(seconds or 0) * 1000))
        if delay_ms <= 0:
            return
        self._page.wait_for_timeout(delay_ms)
        self.current_url = str(getattr(self._page, "url", "") or self.current_url)
        self._page_source = str(self._page.content() or self._page_source)

    def get_title(self) -> str:
        try:
            return str(self._page.title() or "")
        except Exception:
            return ""

    def quit(self) -> None:
        self.quit_called = True
        try:
            self._context.close()
        except Exception:
            pass
        try:
            self._browser.close()
        except Exception:
            pass
        try:
            self._playwright.stop()
        except Exception:
            pass


def is_blocked(page_content: str, title: str) -> dict[str, object]:
    normalized_html = _normalize_computrabajo_text(page_content)
    normalized_title = _normalize_computrabajo_text(title)
    html_length = len((page_content or "").strip())
    has_forbidden_signal = any(
        token in normalized_html or token in normalized_title
        for token in ("403 forbidden", "access denied", "forbidden")
    )
    has_simple_markup = any(
        token in normalized_html
        for token in ("<article", "/oferta/", "/ofertas/", "/oferta-de-trabajo", "<h2", "<h3")
    )
    blocked = has_forbidden_signal or (html_length < 500 and not has_simple_markup)
    return {
        "blocked": blocked,
        "reason": "computrabajo_403" if blocked else "",
    }


class ComputrabajoPlaywrightJobScraper(PlaywrightJobScraper, ComputrabajoJobScraper):
    portal_name = "computrabajo_playwright"
    mode = COMPUTRABAJO_PLAYWRIGHT_MODE

    def scrape(self, source) -> list[ScrapedJob]:
        requested_url = self.build_search_url(source)
        result_portal = self._result_portal_name(source)
        self._debug_log(f"[computrabajo] mode={self.mode}")
        self._debug_log("[computrabajo] using_raw_playwright=true")
        self._debug_log("[computrabajo] adapter_bypassed=true")
        self._debug_log("[computrabajo] isolated_mode_active=true")
        if not getattr(self.settings, "enable_playwright", False):
            self._record_search_snapshot(
                requested_url,
                requested_url,
                "",
                status_code=None,
                block_reason="playwright desactivado",
            )
            response = self._make_state_response(requested_url, None)
            self._log_search_state(COMPUTRABAJO_STATE_SCRAPER_BROKEN, response, "", [])
            logger.warning("[%s] playwright_disabled url=%s", result_portal, requested_url)
            return []

        html = self.fetch_search_results(source)
        results: list[ScrapedJob] = []
        for item in self.parse_search_results(html, source):
            if not item.title or not item.url:
                continue
            item.portal = result_portal
            item.source_id = source.id
            item.found_at = item.found_at or datetime.now(UTC)
            results.append(item)
            if len(results) >= self.settings.max_results_per_source:
                break
        if not results:
            self._debug_log("[computrabajo] parsed_jobs=0")
        return results

    def fetch_search_results(self, source) -> str:
        requested_url = self.build_search_url(source)
        result_portal = self._result_portal_name(source)
        self._debug_log(f"[computrabajo] mode={self.mode}")
        self._debug_log("[computrabajo] using_raw_playwright=true")
        self._debug_log("[computrabajo] adapter_bypassed=true")
        self._debug_log("[computrabajo] isolated_mode_active=true")
        if not getattr(self.settings, "enable_playwright", False):
            self._record_search_snapshot(
                requested_url,
                requested_url,
                "",
                status_code=None,
                block_reason="playwright desactivado",
            )
            response = self._make_state_response(requested_url, None)
            self._log_search_state(COMPUTRABAJO_STATE_SCRAPER_BROKEN, response, "", [])
            logger.warning("[%s] playwright_disabled url=%s", result_portal, requested_url)
            return ""

        try:
            driver = self._build_driver()
        except Exception as exc:
            logger.warning("[%s] playwright_driver_error=%s", result_portal, exc)
            return self._handle_search_failure(requested_url, f"playwright driver error: {exc}")

        try:
            html, final_url, status_code, title = self._load_rendered_html(driver, requested_url)
            soup = self._soup(html) if html else self._soup("")
            job_cards = self._extract_simple_job_cards(soup) if html else []
            block_state = is_blocked(html, title)
            self._record_search_snapshot(requested_url, final_url, html, status_code=status_code)
            self._debug_log(f"[computrabajo] final_url={final_url}")
            self._debug_log(f"[computrabajo] html_length={len(html)}")
            self._debug_log(f"[computrabajo] job_containers_before_parse={len(job_cards)}")
            self._debug_log(f"[computrabajo] blocked={'true' if block_state['blocked'] else 'false'}")
            if block_state["blocked"]:
                self._set_block_reason(str(block_state["reason"]))
                if self.last_response_debug is not None:
                    self.last_response_debug.block_reason = str(block_state["reason"])
                self._debug_log(f"[computrabajo] html_snippet={self._html_snippet(html)}")
                self._debug_log("Computrabajo blocked page detected", level="warning")
                return ""
            self._debug_log("[computrabajo] extracting_jobs_from_rendered_html")
            return html
        except (CaptchaRequiredError, LoginRequiredError, SourceBlockedError):
            raise
        except Exception as exc:
            current_url = getattr(driver, "current_url", "") or requested_url
            current_html = getattr(driver, "page_source", "") or ""
            logger.warning("[%s] playwright_search_error=%s", result_portal, exc)
            return self._handle_search_failure(
                requested_url,
                f"playwright search error: {exc}",
                final_url=current_url,
                html=current_html,
            )
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    def fetch_job_detail(self, job: ScrapedJob, source) -> ScrapedJob:
        return job

    def build_search_url(self, source) -> str:
        return str(getattr(source, "search_url", "") or "")

    def parse_search_results(self, html: str, source) -> list[ScrapedJob]:
        if not html:
            return []
        soup = self._soup(html)
        cards = self._extract_simple_job_cards(soup)
        self._debug_log(f"[computrabajo] job_containers_before_parse={len(cards)}")
        if not cards:
            self._debug_log(f"[computrabajo] html_snippet={self._html_snippet(html)}")
        results: list[ScrapedJob] = []
        seen_urls: set[str] = set()
        found_at = datetime.now(UTC)
        for card in cards:
            anchor = self._extract_simple_anchor(card)
            if anchor is None or not anchor.has_attr("href"):
                continue
            href = self._clean_text(str(anchor.get("href", "")))
            if not href:
                continue
            absolute_url = self._absolute_url(source, href)
            normalized_url = self.normalize_url(absolute_url)
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            title = self._extract_simple_title(card, anchor)
            if not title:
                continue
            company = self._extract_simple_company(card, title)
            location = self._extract_simple_location(card, title, company)
            salary = self._extract_simple_salary(card, title, company, location)
            description = self._extract_simple_description(card, title, company, location, salary)
            raw_posted_text = self._extract_card_posted_text(card)
            results.append(
                ScrapedJob(
                    title=title,
                    company=company,
                    portal=self.portal_name,
                    location=location,
                    modality=self._infer_modality(location, description),
                    salary=salary,
                    url=absolute_url,
                    description=description,
                    requirements="",
                    published_at=self._parse_published_at(raw_posted_text),
                    found_at=found_at,
                    raw_posted_text=raw_posted_text,
                    source_id=source.id,
                )
            )
        return results

    def _build_driver(self):
        if self.driver_factory is not None:
            return self.driver_factory()
        timeout_seconds = getattr(self.settings, "playwright_page_load_timeout", getattr(self.settings, "scraper_timeout", 20))
        return _RawComputrabajoPlaywrightDriver(timeout_seconds)

    def _load_rendered_html(self, driver, requested_url: str) -> tuple[str, str, int | None, str]:
        driver.set_page_load_timeout(self.settings.playwright_page_load_timeout)
        self._debug_log(f"[computrabajo] playwright_open_url={requested_url}")
        driver.get(requested_url)
        self._settle_after_navigation(driver)
        html = getattr(driver, "page_source", "") or ""
        final_url = getattr(driver, "current_url", "") or requested_url
        status_code = getattr(driver, "last_status_code", None)
        title = self._read_page_title(driver, html) or "(sin titulo)"
        self._debug_log("[computrabajo] page_loaded")
        self._debug_log(f"[computrabajo] final_url={final_url}")
        self._debug_log(f"[computrabajo] html_length={len(html)}")
        self._debug_log(f"[computrabajo] page_title={title}")
        return html, final_url, status_code or (200 if html else None), title

    def _settle_after_navigation(self, driver) -> None:
        wait_for_timeout = getattr(driver, "wait_for_timeout", None)
        if callable(wait_for_timeout):
            wait_for_timeout(COMPUTRABAJO_SIMPLE_WAIT_SECONDS)

    def _extract_simple_job_cards(self, soup: BeautifulSoup) -> list[Tag]:
        cards: list[Tag] = []
        for selector in COMPUTRABAJO_SIMPLE_CARD_SELECTORS:
            for node in soup.select(selector):
                if not isinstance(node, Tag):
                    continue
                if self._extract_simple_anchor(node) is None:
                    continue
                cards.append(node)
        if cards:
            return self._dedupe_simple_cards(cards)

        fallback_cards: list[Tag] = []
        for anchor in soup.select(", ".join(COMPUTRABAJO_SIMPLE_LINK_SELECTORS)):
            if not isinstance(anchor, Tag):
                continue
            container = anchor.find_parent("article")
            if container is None:
                container = anchor.find_parent(["div", "section", "li"]) or anchor
            fallback_cards.append(container)
        return self._dedupe_simple_cards(fallback_cards)

    def _dedupe_simple_cards(self, cards: list[Tag]) -> list[Tag]:
        deduped: dict[str, Tag] = {}
        for card in cards:
            anchor = self._extract_simple_anchor(card)
            if anchor is None:
                continue
            href = self._clean_text(str(anchor.get("href", "")))
            if not href:
                continue
            deduped.setdefault(href, card)
        return list(deduped.values())

    def _extract_simple_anchor(self, card: Tag) -> Tag | None:
        for selector in (
            "h2 a[href]",
            "h3 a[href]",
            *COMPUTRABAJO_SIMPLE_LINK_SELECTORS,
            "a[href]",
        ):
            anchor = card.select_one(selector)
            if isinstance(anchor, Tag) and anchor.has_attr("href"):
                return anchor
        return None

    def _extract_simple_title(self, card: Tag, anchor: Tag) -> str:
        title = self._first_text(card, ("h2", "h3"))
        if title:
            return title
        return self._clean_text(anchor.get_text(" ", strip=True))

    def _extract_simple_company(self, card: Tag, title: str) -> str:
        company = self._first_text(card, (".fs16.fc_base.mt5", "[class*='company']", "[class*='empresa']"))
        if company:
            return company
        return self._first_simple_text(card, exclude={title})

    def _extract_simple_location(self, card: Tag, title: str, company: str) -> str:
        location = self._first_text(card, (".fs13.fc_aux.mt15", "[class*='location']", "[class*='ubicacion']", "[class*='ciudad']"))
        if location:
            return location
        return self._first_simple_text(card, exclude={title, company}, prefer_location=True)

    def _extract_simple_salary(self, card: Tag, title: str, company: str, location: str) -> str:
        salary = self._first_text(card, ("[class*='salary']", "[class*='salario']", ".salary"))
        if salary:
            return salary
        for node in card.find_all(["span", "p", "div", "small"]):
            text = self._clean_text(node.get_text(" ", strip=True))
            if text in {title, company, location}:
                continue
            if "$" in text or "cop" in text.casefold():
                return text
        return ""

    def _extract_simple_description(
        self,
        card: Tag,
        title: str,
        company: str,
        location: str,
        salary: str,
    ) -> str:
        excluded = {title, company, location, salary}
        lines: list[str] = []
        for node in card.find_all(["p", "span", "div", "small"]):
            text = self._clean_text(node.get_text(" ", strip=True))
            if not text or text in excluded or text in lines:
                continue
            if len(text.split()) < 4:
                continue
            if "$" in text or "cop" in text.casefold():
                continue
            lines.append(text)
        return "\n".join(lines[:2])

    def _first_simple_text(self, card: Tag, *, exclude: set[str], prefer_location: bool = False) -> str:
        for node in card.find_all(["p", "span", "div", "small"]):
            text = self._clean_text(node.get_text(" ", strip=True))
            if not text or text in exclude:
                continue
            if prefer_location:
                normalized = text.casefold()
                if any(token in normalized for token in ("bogot", "medell", "cali", "barranquilla", "colombia", "remot", "hibrid", "presencial")):
                    return text
                continue
            return text
        return ""

    def _read_page_title(self, driver, html: str) -> str:
        get_title = getattr(driver, "get_title", None)
        if callable(get_title):
            title = self._clean_text(get_title())
            if title:
                return title
        soup = BeautifulSoup(html or "", "html.parser")
        title_node = soup.find("title")
        if title_node is not None:
            title = self._clean_text(title_node.get_text(" ", strip=True))
            if title:
                return title
        heading = soup.find(["h1", "h2"])
        if heading is not None:
            return self._clean_text(heading.get_text(" ", strip=True))
        return ""

    def _html_snippet(self, html: str) -> str:
        collapsed = " ".join((html or "").split())
        return collapsed[:1000]

    def _debug_log(self, message: str, *, level: str = "info") -> None:
        log_fn = getattr(logger, level, logger.info)
        log_fn(message)
        self._log_playwright(message)

    def _handle_search_failure(
        self,
        requested_url: str,
        reason: str,
        *,
        final_url: str = "",
        html: str = "",
    ) -> str:
        resolved_url = final_url or requested_url
        self._record_search_snapshot(
            requested_url,
            resolved_url,
            html,
            status_code=None if not html else 200,
            block_reason=reason,
        )
        if self.last_response_debug is not None:
            self.last_response_debug.block_reason = reason
        response = self._make_state_response(resolved_url, None if not html else 200)
        self._log_search_state(COMPUTRABAJO_STATE_SCRAPER_BROKEN, response, html, [])
        logger.warning("[%s] playwright_failure=%s", self.portal_name, reason)
        return ""
