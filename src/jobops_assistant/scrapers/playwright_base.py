from __future__ import annotations

import os
from pathlib import Path
from time import sleep

from .base_scraper import ResponseDebugSnapshot, ScrapedJob, SelectorBasedScraper, SourceBlockedError

DEFAULT_PLAYWRIGHT_USER_DATA_DIR = "./data/browser_profiles/playwright_linkedin"
PLAYWRIGHT_DISABLED_MESSAGE = "Playwright esta desactivado. Activa JOBOPS_ENABLE_PLAYWRIGHT=true para usar este scraper."
PLAYWRIGHT_INSTALL_MESSAGE = (
    "Playwright no esta instalado. Ejecuta pip install -r requirements.txt y python -m playwright install chromium."
)
PLAYWRIGHT_PROFILE_IN_USE_MESSAGE = (
    "El perfil persistente de Playwright esta en uso. Cierra las ventanas del navegador o usa otra carpeta."
)
LINKEDIN_SESSION_COOKIE_NAMES = {"li_at", "liap", "JSESSIONID"}
RAW_PLAYWRIGHT_PORTALS = {"computrabajo", "computrabajo_playwright"}


def _emit_driver_log(enabled: bool, message: str) -> None:
    if enabled:
        print(message)


class PlaywrightElementAdapter:
    def __init__(self, locator) -> None:
        self.locator = locator

    @property
    def text(self) -> str:
        try:
            return str(self.locator.inner_text(timeout=1000) or "")
        except Exception:
            return ""

    def is_displayed(self) -> bool:
        try:
            return bool(self.locator.is_visible(timeout=1000))
        except Exception:
            return False

    def is_enabled(self) -> bool:
        try:
            return bool(self.locator.is_enabled(timeout=1000))
        except Exception:
            return False

    def get_attribute(self, name: str) -> str:
        try:
            return str(self.locator.get_attribute(name, timeout=1000) or "")
        except Exception:
            return ""

    def click(self) -> None:
        self.locator.click(timeout=1500)


class PlaywrightBrowserSession:
    def __init__(
        self,
        settings,
        *,
        headless: bool | None = None,
        user_data_dir: str | None = None,
    ) -> None:
        self.settings = settings
        self.headless = getattr(settings, "playwright_headless", True) if headless is None else headless
        configured_user_data_dir = (
            getattr(settings, "playwright_user_data_dir", DEFAULT_PLAYWRIGHT_USER_DATA_DIR)
            if user_data_dir is None
            else user_data_dir
        )
        self.user_data_dir = self._expand_browser_setting(configured_user_data_dir)
        self.timeout_seconds = max(
            1,
            int(getattr(settings, "playwright_page_load_timeout", getattr(settings, "scraper_timeout", 20)) or 1),
        )
        self.timeout_ms = self.timeout_seconds * 1000
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def get_page(self, url: str | None = None):
        page = self._ensure_page()
        if url:
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        return page

    def extract_html(self) -> str:
        page = self._page
        if page is None:
            return ""
        try:
            return str(page.content() or "")
        except Exception:
            return ""

    def get_context(self):
        return self._ensure_context()

    def get_cookies(self, urls: list[str] | None = None) -> list[dict]:
        context = self._ensure_context()
        try:
            if urls:
                return list(context.cookies(urls))
            return list(context.cookies())
        except Exception:
            return []

    def has_linkedin_session_cookie(self) -> bool:
        for cookie in self.get_cookies(["https://www.linkedin.com"]):
            name = str(cookie.get("name", "") or "")
            domain = str(cookie.get("domain", "") or "").casefold()
            if name in LINKEDIN_SESSION_COOKIE_NAMES and "linkedin.com" in domain:
                return True
        return False

    def save_storage_state(self, path: str | Path | None = None) -> str:
        context = self._ensure_context()
        target_path = Path(path) if path is not None else Path(self.user_data_dir) / "storage_state.json"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(target_path))
        return str(target_path)

    def page_count(self) -> int:
        context = self._context
        if context is None:
            return 0
        try:
            return len(list(getattr(context, "pages", []) or []))
        except Exception:
            return 0

    def close(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._page = None

    def _ensure_page(self):
        context = self._ensure_context()
        if self._page is None:
            existing_pages = list(getattr(context, "pages", []) or [])
            self._page = existing_pages[0] if existing_pages else context.new_page()
            self._page.set_default_timeout(self.timeout_ms)
            self._page.set_default_navigation_timeout(self.timeout_ms)
        return self._page

    def _ensure_context(self):
        if self._context is not None:
            return self._context
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - exercised via friendly error path.
            raise SourceBlockedError(PLAYWRIGHT_INSTALL_MESSAGE) from exc

        self._playwright = sync_playwright().start()
        launch_args = [
            "--lang=es-CO",
            "--disable-blink-features=AutomationControlled",
        ]
        try:
            if self.user_data_dir:
                Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
                self._context = self._playwright.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    headless=self.headless,
                    locale="es-CO",
                    user_agent=self.settings.scraper_user_agent,
                    args=launch_args,
                )
            else:
                self._browser = self._playwright.chromium.launch(
                    headless=self.headless,
                    args=launch_args,
                )
                self._context = self._browser.new_context(
                    locale="es-CO",
                    user_agent=self.settings.scraper_user_agent,
                )
        except Exception as exc:
            self.close()
            if self._is_profile_in_use_error(exc):
                raise SourceBlockedError(PLAYWRIGHT_PROFILE_IN_USE_MESSAGE) from exc
            raise
        return self._context

    @staticmethod
    def _expand_browser_setting(value: str) -> str:
        raw_value = str(value or "").strip().strip("\"'")
        if not raw_value:
            return ""
        return os.path.expanduser(os.path.expandvars(raw_value))

    @staticmethod
    def _is_profile_in_use_error(exc: Exception) -> bool:
        message = str(exc).casefold()
        return (
            "user data directory is already in use" in message
            or "process singleton" in message
            or "profile appears to be in use" in message
            or "cannot read and write to its data directory" in message
        )


class PlaywrightDriverAdapter:
    def __init__(
        self,
        settings,
        *,
        log_playwright: bool = True,
        headless: bool | None = None,
        user_data_dir: str | None = None,
    ) -> None:
        self.settings = settings
        self.log_playwright = log_playwright
        self.session = PlaywrightBrowserSession(settings, headless=headless, user_data_dir=user_data_dir)
        self.page = None
        self.timeout = max(
            1,
            int(getattr(settings, "playwright_page_load_timeout", getattr(settings, "scraper_timeout", 20)) or 1),
        )
        self.current_url = ""
        self.visited_urls: list[str] = []
        self.quit_called = False

    @property
    def page_source(self) -> str:
        html = self.session.extract_html()
        if self.page is not None:
            try:
                self.current_url = str(self.page.url or self.current_url)
            except Exception:
                pass
        return html

    def set_page_load_timeout(self, timeout: int) -> None:
        self.timeout = max(1, int(timeout or 1))
        self.session.timeout_seconds = self.timeout
        self.session.timeout_ms = self.timeout * 1000
        if self.page is not None:
            try:
                self.page.set_default_timeout(self.session.timeout_ms)
                self.page.set_default_navigation_timeout(self.session.timeout_ms)
            except Exception:
                pass

    def get(self, url: str) -> None:
        self.visited_urls.append(url)
        self.page = self.session.get_page(url)
        self.current_url = str(getattr(self.page, "url", "") or url)

    def execute_script(self, script: str) -> None:
        if self.page is None:
            return
        try:
            self.page.evaluate(script)
        except Exception:
            try:
                self.page.mouse.wheel(0, 3000)
            except Exception:
                pass
        sleep(0.1)

    def find_elements(self, by, selector: str):
        if self.page is None or not selector:
            return []
        try:
            locator = self.page.locator(selector)
            count = locator.count()
        except Exception:
            return []
        return [PlaywrightElementAdapter(locator.nth(index)) for index in range(count)]

    def wait_for_any_selector(self, selectors: tuple[str, ...], *, timeout: int) -> bool:
        if self.page is None:
            return False
        timeout_ms = max(1, int(timeout or self.timeout)) * 1000
        for selector in selectors:
            try:
                self.page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

    def wait_for_load_state(self, state: str = "load", *, timeout: int | None = None) -> bool:
        if self.page is None:
            return False
        timeout_ms = max(1, int(timeout or self.timeout)) * 1000
        try:
            self.page.wait_for_load_state(state, timeout=timeout_ms)
            return True
        except Exception:
            return False

    def wait_for_timeout(self, seconds: float) -> None:
        delay_ms = max(0, int(float(seconds or 0) * 1000))
        if delay_ms <= 0:
            return
        if self.page is not None:
            try:
                self.page.wait_for_timeout(delay_ms)
                return
            except Exception:
                pass
        sleep(delay_ms / 1000)

    def get_title(self) -> str:
        if self.page is None:
            return ""
        try:
            return str(self.page.title() or "")
        except Exception:
            return ""

    def quit(self) -> None:
        self.quit_called = True
        self.session.close()


class RawPlaywrightDriver:
    def __init__(
        self,
        settings,
        *,
        log_playwright: bool = True,
    ) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - exercised via friendly error path.
            raise SourceBlockedError(PLAYWRIGHT_INSTALL_MESSAGE) from exc

        self.settings = settings
        self.log_playwright = log_playwright
        self.timeout = max(
            1,
            int(getattr(settings, "playwright_page_load_timeout", getattr(settings, "scraper_timeout", 20)) or 1),
        )
        self.timeout_ms = self.timeout * 1000
        self.current_url = ""
        self.last_status_code: int | None = None
        self.quit_called = False
        self._page_source = ""
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=False)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        self.set_page_load_timeout(self.timeout)

    @property
    def page_source(self) -> str:
        return self._page_source

    def set_page_load_timeout(self, timeout: int) -> None:
        self.timeout = max(1, int(timeout or 1))
        self.timeout_ms = self.timeout * 1000
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

    def execute_script(self, script: str) -> None:
        try:
            self._page.evaluate(script)
        except Exception:
            try:
                self._page.mouse.wheel(0, 2400)
            except Exception:
                pass
        self._page_source = str(self._page.content() or self._page_source)
        sleep(0.1)

    def find_elements(self, by, selector: str):
        if not selector:
            return []
        try:
            locator = self._page.locator(selector)
            count = locator.count()
        except Exception:
            return []
        return [PlaywrightElementAdapter(locator.nth(index)) for index in range(count)]

    def wait_for_any_selector(self, selectors: tuple[str, ...], *, timeout: int) -> bool:
        timeout_ms = max(1, int(timeout or self.timeout)) * 1000
        for selector in selectors:
            try:
                self._page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
                self._page_source = str(self._page.content() or self._page_source)
                self.current_url = str(getattr(self._page, "url", "") or self.current_url)
                return True
            except Exception:
                continue
        return False

    def wait_for_load_state(self, state: str = "load", *, timeout: int | None = None) -> bool:
        timeout_ms = max(1, int(timeout or self.timeout)) * 1000
        try:
            self._page.wait_for_load_state(state, timeout=timeout_ms)
            self._page_source = str(self._page.content() or self._page_source)
            self.current_url = str(getattr(self._page, "url", "") or self.current_url)
            return True
        except Exception:
            return False

    def wait_for_timeout(self, seconds: float) -> None:
        delay_ms = max(0, int(float(seconds or 0) * 1000))
        if delay_ms <= 0:
            return
        self._page.wait_for_timeout(delay_ms)
        self._page_source = str(self._page.content() or self._page_source)
        self.current_url = str(getattr(self._page, "url", "") or self.current_url)

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


def build_playwright_driver(settings, portal_name: str, *, log_playwright: bool = True):
    normalized_portal = str(portal_name or "").strip().casefold()
    if normalized_portal in RAW_PLAYWRIGHT_PORTALS:
        _emit_driver_log(log_playwright, "[driver] computrabajo_adapter_bypassed=true")
        _emit_driver_log(log_playwright, "[driver] using_raw_playwright_only=true")
        return RawPlaywrightDriver(settings, log_playwright=log_playwright)
    return PlaywrightDriverAdapter(settings, log_playwright=log_playwright)


class PlaywrightJobScraper(SelectorBasedScraper):
    portal_name = "playwright"
    ready_selectors = ("body",)

    def __init__(self, settings, driver_factory=None, *, log_playwright: bool = True) -> None:
        super().__init__(settings)
        self.driver_factory = driver_factory
        self.log_playwright = log_playwright

    def scrape(self, source) -> list[ScrapedJob]:
        html = self.fetch_search_results(source)
        results: list[ScrapedJob] = []
        result_portal = self._result_portal_name(source)
        for item in self.parse_search_results(html, source):
            if not item.title or not item.url:
                continue
            item.url = self.normalize_url(item.url)
            job = self.fetch_job_detail(item, source)
            job.url = self.normalize_url(job.url)
            job.portal = result_portal
            job.source_id = source.id
            results.append(job)
            if len(results) >= self.settings.max_results_per_source:
                break
        return results

    def fetch_search_results(self, source) -> str:
        if not getattr(self.settings, "enable_playwright", False):
            requested_url = self.build_search_url(source)
            self.last_response_debug = ResponseDebugSnapshot(
                requested_url=requested_url,
                status_code=None,
                final_url=requested_url,
                content_type="text/html",
                html="",
                block_reason="playwright desactivado",
            )
            raise SourceBlockedError(PLAYWRIGHT_DISABLED_MESSAGE)

        driver = self._build_driver()
        requested_url = self.build_search_url(source)
        try:
            html, final_url, status_code = self._load_rendered_html(driver, requested_url, self.ready_selectors)
            self.last_response_debug = ResponseDebugSnapshot(
                requested_url=requested_url,
                status_code=status_code,
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
        return build_playwright_driver(self.settings, self.portal_name, log_playwright=self.log_playwright)

    def _is_fast_mode_enabled(self) -> bool:
        return bool(getattr(self.settings, "playwright_fast_mode", True))

    def _get_configured_page_load_timeout(self) -> int:
        return max(
            1,
            int(getattr(self.settings, "playwright_page_load_timeout", getattr(self.settings, "scraper_timeout", 20)) or 1),
        )

    def _get_effective_page_load_timeout(self) -> int:
        return self._get_configured_page_load_timeout()

    def _get_effective_wait_timeout(self) -> int:
        return self._get_effective_page_load_timeout()

    def _get_playwright_retry_attempts(self) -> int:
        return 1 if self._is_fast_mode_enabled() else 2

    def _get_effective_scroll_pause(self) -> float:
        configured_pause = max(0.0, float(getattr(self.settings, "playwright_scroll_pause", 0) or 0.0))
        if self._is_fast_mode_enabled():
            if configured_pause <= 0:
                return 0.25
            return min(0.5, configured_pause)
        return max(0.25, configured_pause)

    def _get_effective_max_scrolls(self) -> int:
        configured_scrolls = max(0, int(getattr(self.settings, "playwright_max_scrolls", 0) or 0))
        if self._is_fast_mode_enabled():
            return 1 if configured_scrolls <= 0 else min(2, configured_scrolls)
        return configured_scrolls

    def _should_return_early_after_initial_load(self, html: str) -> bool:
        if not html:
            return False
        try:
            return self.has_public_job_content(html)
        except Exception:
            return False

    def _load_rendered_html(self, driver, requested_url: str, selectors: tuple[str, ...]) -> tuple[str, str, int | None]:
        driver.set_page_load_timeout(self._get_effective_page_load_timeout())
        last_error: Exception | None = None
        attempt_count = self._get_playwright_retry_attempts()
        for attempt in range(attempt_count):
            try:
                self._navigate_to_url(driver, requested_url)
                self._wait_for_rendered_dom(driver, selectors)
                html = getattr(driver, "page_source", "") or ""
                final_url = getattr(driver, "current_url", "") or requested_url
                if self._should_return_early_after_initial_load(html):
                    self._log_playwright("Playwright: early exit after initial card detection")
                    title = ""
                    get_title = getattr(driver, "get_title", None)
                    if callable(get_title):
                        title = str(get_title() or "")
                    if title:
                        self._log_playwright(f"Playwright: page_title={title}")
                    self._log_playwright(f"Playwright: html_length={len(html)}")
                    return html, final_url, 200 if html else None
                self._scroll_page(driver)
                html = getattr(driver, "page_source", "") or ""
                final_url = getattr(driver, "current_url", "") or requested_url
                title = ""
                get_title = getattr(driver, "get_title", None)
                if callable(get_title):
                    title = str(get_title() or "")
                if title:
                    self._log_playwright(f"Playwright: page_title={title}")
                self._log_playwright(f"Playwright: html_length={len(html)}")
                return html, final_url, 200 if html else None
            except Exception as exc:
                last_error = exc
                current_html = getattr(driver, "page_source", "") or ""
                current_url = getattr(driver, "current_url", "") or requested_url
                if self._is_timeout_error(exc) and attempt + 1 < attempt_count:
                    self._log_playwright(f"Playwright: timeout, retry={attempt + 1} url={requested_url}")
                    continue
                if current_html and self.has_public_job_content(current_html):
                    self._log_playwright(f"Playwright: carga parcial reutilizada tras error en {requested_url}")
                    return current_html, current_url, 200
                break
        raise RuntimeError(str(last_error) if last_error is not None else "playwright load failed")

    def _navigate_to_url(self, driver, url: str) -> None:
        self._log_playwright(f"Playwright: navigating URL: {url}")
        driver.get(url)
        self._log_playwright(f"Playwright: current_url after get: {getattr(driver, 'current_url', '')}")

    def _wait_for_rendered_dom(self, driver, selectors: tuple[str, ...]) -> None:
        timeout = self._get_effective_wait_timeout()
        stable_wait_seconds = self._get_effective_scroll_pause()
        wait_for_load_state = getattr(driver, "wait_for_load_state", None)
        wait_for_timeout = getattr(driver, "wait_for_timeout", None)
        if callable(wait_for_load_state):
            wait_for_load_state("domcontentloaded", timeout=timeout)
        wait_supported = getattr(driver, "wait_for_any_selector", None)
        if callable(wait_supported) and wait_supported(selectors, timeout=timeout):
            if callable(wait_for_timeout):
                wait_for_timeout(stable_wait_seconds)
            return

        if callable(wait_for_timeout):
            wait_for_timeout(stable_wait_seconds)
            return

        pause = max(0.0, float(getattr(self.settings, "playwright_scroll_pause", 0) or 0))
        if pause:
            sleep(pause)

    def _scroll_page(self, driver) -> None:
        pause = self._get_effective_scroll_pause()
        max_scrolls = self._get_effective_max_scrolls()
        wait_for_timeout = getattr(driver, "wait_for_timeout", None)
        if callable(wait_for_timeout):
            wait_for_timeout(pause)
        elif pause:
            sleep(pause)
        for _ in range(max_scrolls):
            driver.execute_script("window.scrollBy(0, Math.max(500, Math.floor(window.innerHeight * 0.85)));")
            if callable(wait_for_timeout):
                wait_for_timeout(pause)
            elif pause:
                sleep(pause)
            html = getattr(driver, "page_source", "") or ""
            if self._should_return_early_after_initial_load(html):
                self._log_playwright("Playwright: stopping scroll early after card detection")
                break

    def _log_playwright(self, message: str) -> None:
        if self.log_playwright:
            print(message)

    def _is_timeout_error(self, exc: Exception) -> bool:
        message = str(exc).casefold()
        name = exc.__class__.__name__.casefold()
        return isinstance(exc, TimeoutError) or "timeout" in name or "timed out" in message or "timeout" in message

    def _result_portal_name(self, source) -> str:
        return str(getattr(source, "portal", "") or self.portal_name).strip().lower() or self.portal_name
