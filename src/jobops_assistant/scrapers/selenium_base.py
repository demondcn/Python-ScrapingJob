from __future__ import annotations

from time import sleep

from bs4 import BeautifulSoup

from .base_scraper import (
    CaptchaRequiredError,
    LoginRequiredError,
    ResponseDebugSnapshot,
    SelectorBasedScraper,
    SourceBlockedError,
)

SELENIUM_DISABLED_MESSAGE = "Selenium está desactivado. Activa JOBOPS_ENABLE_SELENIUM=true para usar este scraper."


class SeleniumJobScraper(SelectorBasedScraper):
    """Selector-based scraper that gets HTML through Selenium for public pages."""

    portal_name = "selenium"

    def __init__(self, settings, driver_factory=None) -> None:
        super().__init__(settings)
        self.driver_factory = driver_factory

    def fetch_search_results(self, source) -> str:
        if not self.settings.enable_selenium:
            self.last_response_debug = ResponseDebugSnapshot(
                requested_url=self.build_search_url(source),
                status_code=None,
                final_url=self.build_search_url(source),
                content_type="text/html",
                html="",
                block_reason="selenium desactivado",
            )
            raise SourceBlockedError(SELENIUM_DISABLED_MESSAGE)

        driver = self._build_driver()
        requested_url = self.build_search_url(source)
        try:
            driver.set_page_load_timeout(self.settings.selenium_page_load_timeout)
            driver.get(requested_url)
            self._scroll_page(driver)
            html = getattr(driver, "page_source", "") or ""
            final_url = getattr(driver, "current_url", "") or requested_url
            self.last_response_debug = ResponseDebugSnapshot(
                requested_url=requested_url,
                status_code=None,
                final_url=self._clean_text(final_url),
                content_type="text/html",
                html=html,
            )
            self._detect_blocked_content(html)
            return html
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    def _build_driver(self):
        if self.driver_factory is not None:
            return self.driver_factory()

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except ImportError as exc:
            raise SourceBlockedError("Selenium no está instalado. Ejecuta pip install -r requirements.txt.") from exc

        options = Options()
        if self.settings.selenium_headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"--user-agent={self.settings.scraper_user_agent}")
        return webdriver.Chrome(options=options)

    def _scroll_page(self, driver) -> None:
        pause = max(0, self.settings.selenium_scroll_pause)
        max_scrolls = max(0, self.settings.selenium_max_scrolls)
        if pause:
            sleep(pause)
        for _ in range(max_scrolls):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            if pause:
                sleep(pause)

    def _detect_blocked_content(self, html: str) -> None:
        soup = self._soup(html)
        visible_text = self._normalize_text(soup.get_text(" ", strip=True))
        html_text = self._normalize_text(html)
        if self.has_public_job_content(html):
            return
        reason = self._detect_selenium_block_reason(soup, visible_text, html_text)
        if reason:
            self._set_block_reason(reason)
            if "login" in reason or "inicio de sesion" in reason:
                raise LoginRequiredError(self.login_error_message)
            raise CaptchaRequiredError(self.captcha_error_message)

    def _detect_selenium_block_reason(self, soup: BeautifulSoup, visible_text: str, html_text: str) -> str:
        text_signals = {
            "security check": "security check",
            "security verification": "security verification",
            "captcha": "captcha",
            "recaptcha": "recaptcha",
            "turnstile": "turnstile",
            "verify you are human": "captcha",
            "access denied": "access denied",
            "forbidden": "forbidden",
            "login required": "login requerido",
            "sign in to continue": "login requerido",
            "sign in to view more jobs": "login requerido",
            "let's sign you in": "login requerido",
            "join linkedin": "login requerido",
            "inicia sesion": "inicio de sesion requerido",
            "iniciar sesion": "inicio de sesion requerido",
        }
        for token, reason in text_signals.items():
            if token in visible_text or token in html_text:
                return reason

        selectors = (
            "iframe[src*='recaptcha']",
            ".g-recaptcha",
            ".cf-turnstile",
            "iframe[src*='turnstile']",
            "form[action*='captcha']",
            "input[name*='captcha']",
        )
        if any(soup.select_one(selector) for selector in selectors):
            return "captcha"
        return ""
