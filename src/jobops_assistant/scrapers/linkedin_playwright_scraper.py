from __future__ import annotations

from time import sleep

from .linkedin_selenium_scraper import (
    LINKEDIN_LOGGED_CARDS_MESSAGE,
    LINKEDIN_PUBLIC_CARDS_MESSAGE,
    LinkedInSeleniumJobScraper,
    has_linkedin_job_cards,
    has_logged_in_linkedin_job_cards,
    has_public_linkedin_job_cards,
)
from .playwright_base import PlaywrightJobScraper

LINKEDIN_PLAYWRIGHT_MANUAL_LOGIN_MESSAGE = (
    "LinkedIn Playwright: inicia sesion manualmente en la ventana abierta y presiona ENTER para continuar."
)


class LinkedInPlaywrightJobScraper(PlaywrightJobScraper, LinkedInSeleniumJobScraper):
    portal_name = "linkedin_playwright"
    ready_selectors = (
        *LinkedInSeleniumJobScraper.card_selectors,
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
        settle_seconds = self._get_linkedin_settle_seconds()
        self._wait_for_timeout(driver, settle_seconds)

    def _scroll_page(self, driver) -> None:
        if self._is_ultra_fast_mode_enabled():
            return
        driver.execute_script("window.scrollBy(0, Math.max(700, Math.floor(window.innerHeight * 0.9)));")
        self._click_show_more_button(driver)
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
        for selector in LinkedInSeleniumJobScraper.card_selectors:
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

    def _build_debug_snapshot(self, requested_url: str, final_url: str, html: str, status_code: int | None):
        from .base_scraper import ResponseDebugSnapshot

        return ResponseDebugSnapshot(
            requested_url=requested_url,
            status_code=status_code,
            final_url=self._clean_text(final_url),
            content_type="text/html",
            html=html,
        )

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

    def _detect_blocked_content(self, html: str) -> None:
        soup = self._soup(html)
        if has_public_linkedin_job_cards(soup):
            print(LINKEDIN_PUBLIC_CARDS_MESSAGE)
            self.session_active = False
            self.session_mode = "public"
            return
        if has_logged_in_linkedin_job_cards(soup):
            print(LINKEDIN_LOGGED_CARDS_MESSAGE)
            self.session_active = True
            self.session_mode = "logged_in"
            return
        super()._detect_blocked_content(html)
