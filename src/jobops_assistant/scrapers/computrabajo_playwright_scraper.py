from __future__ import annotations

import logging
from datetime import UTC, datetime
from time import sleep

from bs4 import BeautifulSoup

from .base_scraper import CaptchaRequiredError, LoginRequiredError, ResponseDebugSnapshot, ScrapedJob, SourceBlockedError
from .computrabajo_scraper import (
    COMPUTRABAJO_READY_SELECTORS,
    COMPUTRABAJO_STATE_BLOCKED,
    COMPUTRABAJO_STATE_OK,
    COMPUTRABAJO_STATE_SCRAPER_BROKEN,
    ComputrabajoJobScraper,
    detect_blocking_state,
    extract_job_cards_robust,
)
from .playwright_base import PlaywrightJobScraper

logger = logging.getLogger(__name__)

COMPUTRABAJO_DOM_STABILIZATION_SECONDS = 2.0
COMPUTRABAJO_SCROLL_PAUSE_SECONDS = 1.0
COMPUTRABAJO_MIN_SCROLLS = 3


class ComputrabajoPlaywrightJobScraper(PlaywrightJobScraper, ComputrabajoJobScraper):
    portal_name = "computrabajo_playwright"

    def __init__(self, settings, driver_factory=None, *, log_playwright: bool = True) -> None:
        super().__init__(settings, driver_factory=driver_factory, log_playwright=log_playwright)
        self._active_driver = None

    def scrape(self, source) -> list[ScrapedJob]:
        requested_url = self.build_search_url(source)
        result_portal = self._result_portal_name(source)
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

        try:
            driver = self._build_driver()
        except Exception as exc:
            logger.warning("[%s] playwright_driver_error=%s", result_portal, exc)
            self._handle_search_failure(requested_url, f"playwright driver error: {exc}")
            return []

        self._active_driver = driver
        try:
            html = self.fetch_search_results(source)
            results: list[ScrapedJob] = []
            for item in self.parse_search_results(html, source):
                if not item.title or not item.url:
                    continue
                item.url = self.normalize_url(item.url)
                job = self.fetch_job_detail(item, source)
                job.url = self.normalize_url(job.url)
                job.portal = result_portal
                job.source_id = source.id
                job.found_at = job.found_at or datetime.now(UTC)
                results.append(job)
                if len(results) >= self.settings.max_results_per_source:
                    break
            if not results:
                self._debug_log("[computrabajo] parsed_jobs=0")
            return results
        finally:
            self._active_driver = None
            try:
                driver.quit()
            except Exception:
                pass

    def build_search_url(self, source) -> str:
        return str(getattr(source, "search_url", "") or "")

    def fetch_search_results(self, source) -> str:
        requested_url = self.build_search_url(source)
        result_portal = self._result_portal_name(source)
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

        driver = self._active_driver
        owned_driver = False
        if driver is None:
            try:
                driver = self._build_driver()
                owned_driver = True
            except Exception as exc:
                logger.warning("[%s] playwright_driver_error=%s", result_portal, exc)
                return self._handle_search_failure(requested_url, f"playwright driver error: {exc}")

        try:
            html, final_url, status_code = self._load_rendered_html(driver, requested_url)
            self._record_search_snapshot(requested_url, final_url, html, status_code=status_code)
            self._debug_log("[computrabajo] extracting_jobs_from_rendered_html")
            soup = BeautifulSoup(html, "html.parser")
            job_cards = extract_job_cards_robust(html, soup)
            response = self._make_state_response(final_url, status_code)
            state = detect_blocking_state(response, html, job_cards)
            self._debug_log(f"[computrabajo] final_url={final_url}")
            self._debug_log(f"[computrabajo] html_length={len(html)}")
            self._debug_log(f"[computrabajo] page_title={self._read_page_title(driver, html) or '(sin titulo)'}")
            self._debug_log(f"[computrabajo] job_containers_before_parse={len(job_cards)}")
            if not job_cards:
                self._debug_log(f"[computrabajo] html_snippet={self._html_snippet(html)}")
            self._debug_log(f"[computrabajo] blocked={'true' if state == COMPUTRABAJO_STATE_BLOCKED else 'false'}")
            self._log_search_state(state, response, html, job_cards)

            if state == COMPUTRABAJO_STATE_BLOCKED:
                self._raise_blocked_search_state(response, html)
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
            if owned_driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def fetch_job_detail(self, job: ScrapedJob, source) -> ScrapedJob:
        if not getattr(self.settings, "enable_playwright", False):
            return job

        driver = self._active_driver
        owned_driver = False
        if driver is None:
            try:
                driver = self._build_driver()
                owned_driver = True
            except Exception as exc:
                logger.warning("[%s] detalle omitido por error: %s (%s)", self.portal_name, job.url, exc)
                return job

        try:
            driver.set_page_load_timeout(self.settings.playwright_page_load_timeout)
            driver.get(job.url)
            self._wait_for_rendered_dom(driver, (".box_detail", "h1", "body"))
            html = getattr(driver, "page_source", "") or ""
            final_url = getattr(driver, "current_url", "") or job.url
            if not html:
                raise RuntimeError("page_source vacio en detalle")
            self.last_response_debug = ResponseDebugSnapshot(
                requested_url=job.url,
                status_code=getattr(driver, "last_status_code", None) or 200,
                final_url=self._clean_text(final_url),
                content_type="text/html",
                html=html,
            )
            self._detect_blocked_content(html)
            enriched = self._parse_job_detail(html, job)
            logger.info("[%s] detalle leido correctamente: %s", self.portal_name, job.url)
            return enriched
        except (SourceBlockedError, CaptchaRequiredError) as exc:
            logger.warning("[%s] detalle omitido por error: %s (%s)", self.portal_name, job.url, exc)
            return job
        except LoginRequiredError as exc:
            logger.warning("[%s] detalle omitido por login/bloqueo: %s (%s)", self.portal_name, job.url, exc)
            return job
        except Exception as exc:
            logger.warning("[%s] detalle omitido por error: %s (%s)", self.portal_name, job.url, exc)
            return job
        finally:
            if owned_driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _load_rendered_html(self, driver, requested_url: str) -> tuple[str, str, int | None]:
        driver.set_page_load_timeout(self.settings.playwright_page_load_timeout)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                self._debug_log(f"[computrabajo] playwright_open_url={requested_url}")
                self._navigate_to_url(driver, requested_url)
                self._wait_for_rendered_dom(driver, COMPUTRABAJO_READY_SELECTORS)
                self._scroll_page(driver)
                self._wait_for_rendered_dom(driver, COMPUTRABAJO_READY_SELECTORS)
                html = getattr(driver, "page_source", "") or ""
                final_url = getattr(driver, "current_url", "") or requested_url
                status_code = getattr(driver, "last_status_code", None) or (200 if html else None)
                self._debug_log("[computrabajo] page_loaded")
                self._debug_log(f"[computrabajo] html_length={len(html)}")
                self._debug_log(f"[computrabajo] final_url={final_url}")
                self._debug_log(f"[computrabajo] page_title={self._read_page_title(driver, html) or '(sin titulo)'}")
                return html, final_url, status_code
            except Exception as exc:
                last_error = exc
                current_html = getattr(driver, "page_source", "") or ""
                current_url = getattr(driver, "current_url", "") or requested_url
                if self._is_timeout_error(exc) and attempt == 0:
                    self._debug_log(f"[computrabajo] playwright_timeout retry=1 url={requested_url}", level="warning")
                    continue
                if current_html and self.has_public_job_content(current_html):
                    self._debug_log(f"[{self.portal_name}] playwright_partial_load_error={exc}", level="warning")
                    self._debug_log("[computrabajo] page_loaded")
                    self._debug_log(f"[computrabajo] html_length={len(current_html)}")
                    self._debug_log(f"[computrabajo] final_url={current_url}")
                    self._debug_log(f"[computrabajo] page_title={self._read_page_title(driver, current_html) or '(sin titulo)'}")
                    return current_html, current_url, 200
                break
        raise RuntimeError(str(last_error) if last_error is not None else "playwright load failed")

    def _wait_for_rendered_dom(self, driver, selectors: tuple[str, ...]) -> None:
        timeout = max(1, int(getattr(self.settings, "playwright_page_load_timeout", 1) or 1))
        wait_for_load_state = getattr(driver, "wait_for_load_state", None)
        wait_for_timeout = getattr(driver, "wait_for_timeout", None)
        if callable(wait_for_load_state):
            wait_for_load_state("domcontentloaded", timeout=timeout)
        wait_supported = getattr(driver, "wait_for_any_selector", None)
        if self._has_any_selector(driver, selectors):
            if callable(wait_for_timeout):
                wait_for_timeout(COMPUTRABAJO_DOM_STABILIZATION_SECONDS)
            return
        if callable(wait_supported) and wait_supported(selectors, timeout=timeout):
            if callable(wait_for_timeout):
                wait_for_timeout(COMPUTRABAJO_DOM_STABILIZATION_SECONDS)
            return
        if callable(wait_for_timeout):
            wait_for_timeout(COMPUTRABAJO_DOM_STABILIZATION_SECONDS)

    def _scroll_page(self, driver) -> None:
        scroll_count = max(
            COMPUTRABAJO_MIN_SCROLLS,
            int(getattr(self.settings, "playwright_max_scrolls", 0) or 0),
        )
        pause = max(
            COMPUTRABAJO_SCROLL_PAUSE_SECONDS,
            float(getattr(self.settings, "playwright_scroll_pause", 0) or 0.0),
        )
        wait_for_timeout = getattr(driver, "wait_for_timeout", None)
        if callable(wait_for_timeout):
            wait_for_timeout(pause)
        elif pause:
            sleep(pause)
        for _ in range(scroll_count):
            driver.execute_script("window.scrollBy(0, Math.max(500, Math.floor(window.innerHeight * 0.85)));")
            if callable(wait_for_timeout):
                wait_for_timeout(pause)
            elif pause:
                sleep(pause)

    def _has_any_selector(self, driver, selectors: tuple[str, ...]) -> bool:
        find_elements = getattr(driver, "find_elements", None)
        if not callable(find_elements):
            return False
        for selector in selectors:
            try:
                if find_elements(None, selector):
                    return True
            except Exception:
                continue
        return False

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
        soup = BeautifulSoup(html, "html.parser") if html else BeautifulSoup("", "html.parser")
        job_cards = extract_job_cards_robust(html, soup) if html else []
        response = self._make_state_response(resolved_url, None if not html else 200)
        state = detect_blocking_state(response, html, job_cards)
        if state == COMPUTRABAJO_STATE_BLOCKED:
            self._log_search_state(state, response, html, job_cards)
            self._raise_blocked_search_state(response, html)
        effective_state = state
        if effective_state == COMPUTRABAJO_STATE_OK and not job_cards:
            effective_state = COMPUTRABAJO_STATE_SCRAPER_BROKEN
        self._debug_log(f"[computrabajo] blocked={'true' if effective_state == COMPUTRABAJO_STATE_BLOCKED else 'false'}")
        self._log_search_state(effective_state, response, html, job_cards)
        logger.warning("[%s] playwright_failure=%s", self.portal_name, reason)
        return html if effective_state == COMPUTRABAJO_STATE_OK else ""

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
