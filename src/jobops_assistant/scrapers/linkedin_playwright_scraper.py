from __future__ import annotations

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
