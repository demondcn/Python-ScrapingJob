from __future__ import annotations

from .indeed_selenium_scraper import IndeedSeleniumJobScraper
from .playwright_base import PlaywrightJobScraper

INDEED_PLAYWRIGHT_LOGIN_URL = "https://secure.indeed.com/account/login"
INDEED_PLAYWRIGHT_MANUAL_LOGIN_MESSAGE = (
    "Indeed Playwright: inicia sesion manualmente en la ventana abierta y presiona ENTER para continuar."
)


class IndeedPlaywrightJobScraper(PlaywrightJobScraper, IndeedSeleniumJobScraper):
    portal_name = "indeed_playwright"
    ready_selectors = (
        "div.job_seen_beacon",
        "div.slider_container",
        "a.tapItem",
        "div[data-jk]",
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
                self._navigate_to_url(driver, INDEED_PLAYWRIGHT_LOGIN_URL)
                print(INDEED_PLAYWRIGHT_MANUAL_LOGIN_MESSAGE)
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
        has_cards = bool(self._select_cards(soup))
        if has_cards:
            return False
        current_url_normalized = self._normalize_text(current_url)
        visible_text = self._normalize_text(soup.get_text(" ", strip=True))
        login_signals = (
            "sign in",
            "iniciar sesion",
            "my jobs sign in",
            "continua con google",
            "continue with google",
        )
        return "/account/login" in current_url_normalized or any(signal in visible_text for signal in login_signals)

    def _is_logged_in_session(self, html: str, current_url: str) -> bool:
        soup = self._soup(html)
        current_url_normalized = self._normalize_text(current_url)
        if "/account/login" in current_url_normalized:
            return False
        visible_text = self._normalize_text(soup.get_text(" ", strip=True))
        logged_in_signals = (
            "my jobs profile signed in",
            "mis empleos perfil",
            "saved jobs",
            "applications",
        )
        return any(signal in visible_text for signal in logged_in_signals) or bool(
            soup.select_one("[data-testid='account-menu'], nav[aria-label*='Account'], a[href*='/m/']")
        )
