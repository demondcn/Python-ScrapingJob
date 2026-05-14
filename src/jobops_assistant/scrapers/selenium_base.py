from __future__ import annotations

import os
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
CHROME_PROFILE_IN_USE_MESSAGE = (
    "El perfil de Chrome está en uso. Cierra todas las ventanas de Chrome o usa un perfil separado."
)


class SeleniumJobScraper(SelectorBasedScraper):
    """Selector-based scraper that gets HTML through Selenium for public pages."""

    portal_name = "selenium"

    def __init__(self, settings, driver_factory=None, *, log_selenium: bool = True) -> None:
        super().__init__(settings)
        self.driver_factory = driver_factory
        self.log_selenium = log_selenium

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
            self._navigate_to_url(driver, requested_url)
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
        self._apply_chrome_options(options)
        try:
            return webdriver.Chrome(options=options)
        except Exception as exc:
            if self._is_chrome_profile_in_use_error(exc):
                raise SourceBlockedError(CHROME_PROFILE_IN_USE_MESSAGE) from exc
            raise

    def _apply_chrome_options(self, options) -> None:
        if self.settings.selenium_headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-session-crashed-bubble")
        options.add_argument("--disable-infobars")
        user_data_dir = self._expand_chrome_setting(getattr(self.settings, "selenium_user_data_dir", ""))
        if user_data_dir:
            self._log_selenium(f"Selenium: usando user-data-dir={user_data_dir}")
            options.add_argument(f"--user-data-dir={user_data_dir}")
        profile_directory = str(getattr(self.settings, "selenium_profile_directory", "") or "").strip()
        if profile_directory:
            self._log_selenium(f"Selenium: usando profile-directory={profile_directory}")
            options.add_argument(f"--profile-directory={profile_directory}")
        options.add_argument(f"--user-agent={self.settings.scraper_user_agent}")

    def _navigate_to_url(self, driver, url: str) -> None:
        self._log_selenium(f"Selenium: navegando a URL: {url}")
        driver.get(url)
        current_url = getattr(driver, "current_url", "") or ""
        self._log_selenium(f"Selenium: current_url después de driver.get: {current_url}")

    def _log_selenium(self, message: str) -> None:
        if self.log_selenium:
            print(message)

    @staticmethod
    def _expand_chrome_setting(value: str) -> str:
        raw_value = str(value or "").strip().strip("\"'")
        if not raw_value:
            return ""
        return os.path.expanduser(os.path.expandvars(raw_value))

    @staticmethod
    def _is_chrome_profile_in_use_error(exc: Exception) -> bool:
        message = str(exc).casefold()
        return (
            "user data directory is already in use" in message
            or "profile appears to be in use" in message
            or "cannot create default profile directory" in message
        )

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
