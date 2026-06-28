from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

from bs4 import BeautifulSoup
import pytest

from src.jobops_assistant import cli as cli_module
from src.jobops_assistant.cli import _handle_playwright_login, _handle_playwright_test
from src.jobops_assistant.search_sources import SourceTestResult
from src.jobops_assistant.scrapers.base_scraper import ScrapedJob
from src.jobops_assistant.scrapers import computrabajo_playwright_scraper as computrabajo_module
from src.jobops_assistant.scrapers import playwright_base as playwright_base_module
from src.jobops_assistant.scrapers.computrabajo_playwright_scraper import ComputrabajoPlaywrightJobScraper
from src.jobops_assistant.scrapers.indeed_playwright_scraper import IndeedPlaywrightJobScraper
from src.jobops_assistant.scrapers.linkedin_playwright_scraper import LinkedInPlaywrightJobScraper
from src.jobops_assistant.scrapers.registry import get_scraper, list_supported_portals, source_uses_persistent_auth
from src.jobops_assistant.settings import Settings


def _settings(
    tmp_path: Path,
    *,
    enable_playwright: bool = True,
    linkedin_fetch_details: bool = False,
    playwright_user_data_dir: str = "./data/browser_profiles/playwright_linkedin",
) -> Settings:
    return Settings(
        db_path=tmp_path / "playwright.db",
        match_threshold=65,
        telegram_bot_token="",
        telegram_chat_id="",
        gmail_email="",
        gmail_app_password="",
        scraper_timeout=5,
        scraper_user_agent="JobOps Test Agent",
        max_results_per_source=25,
        min_monitor_interval_minutes=10,
        telegram_digest_max_jobs=10,
        telegram_max_message_chars=3500,
        templates_dir=tmp_path / "templates",
        generated_dir=tmp_path / "generated",
        enable_playwright=enable_playwright,
        playwright_headless=False,
        playwright_user_data_dir=playwright_user_data_dir,
        playwright_page_load_timeout=1,
        playwright_scroll_pause=0,
        playwright_max_scrolls=2,
        linkedin_fetch_details=linkedin_fetch_details,
    )


def _source(portal: str, url: str = "https://example.com/jobs"):
    source = Namespace(
        id=1,
        portal=portal,
        target_role="backend_junior",
        search_url=url,
        keywords="",
        location="",
        enabled=True,
        interval_minutes=30,
    )
    return source


class _FakeDriver:
    def __init__(
        self,
        html: str,
        *,
        current_url: str = "https://example.com/jobs",
        title: str = "",
    ) -> None:
        self.page_source = html
        self.current_url = current_url
        self.title = title
        self.timeout = None
        self.load_states: list[tuple[str, int | None]] = []
        self.timeout_waits: list[float] = []
        self.visited_urls: list[str] = []
        self.scrolls = 0
        self.quit_called = False

    def set_page_load_timeout(self, timeout: int) -> None:
        self.timeout = timeout

    def get(self, url: str) -> None:
        self.visited_urls.append(url)
        self.current_url = url

    def execute_script(self, script: str) -> None:
        self.scrolls += 1

    def quit(self) -> None:
        self.quit_called = True

    def find_elements(self, by, selector: str):
        try:
            return [object() for _ in BeautifulSoup(self.page_source or "", "html.parser").select(selector)]
        except Exception:
            return []

    def wait_for_any_selector(self, selectors: tuple[str, ...], *, timeout: int) -> bool:
        soup = BeautifulSoup(self.page_source or "", "html.parser")
        return any(soup.select_one(selector) for selector in selectors)

    def wait_for_load_state(self, state: str = "load", *, timeout: int | None = None) -> bool:
        self.load_states.append((state, timeout))
        return True

    def wait_for_timeout(self, seconds: float) -> None:
        self.timeout_waits.append(seconds)
        return None

    def get_title(self) -> str:
        if self.title:
            return self.title
        soup = BeautifulSoup(self.page_source or "", "html.parser")
        title_node = soup.find("title")
        if title_node is None:
            return ""
        return title_node.get_text(" ", strip=True)


class _FakePage:
    def __init__(self, url: str) -> None:
        self.url = url
        self._closed = False

    def is_closed(self) -> bool:
        return self._closed


class _FakeBrowserSession:
    def __init__(self, *, has_session: bool = False, user_data_dir: str = "./data/browser_profiles/playwright_linkedin") -> None:
        self.has_session = has_session
        self.user_data_dir = user_data_dir
        self.visited_urls: list[str] = []
        self.closed = False
        self.saved = False
        self.page = _FakePage("about:blank")

    def has_linkedin_session_cookie(self) -> bool:
        return self.has_session

    def get_page(self, url: str | None = None):
        if url:
            self.visited_urls.append(url)
            self.page.url = url
        return self.page

    def page_count(self) -> int:
        return 0 if self.page.is_closed() else 1

    def save_storage_state(self) -> str:
        self.saved = True
        return f"{self.user_data_dir}/storage_state.json"

    def close(self) -> None:
        self.closed = True


class _MappedDriver(_FakeDriver):
    def __init__(self, pages: dict[str, object], *, current_url: str = "about:blank") -> None:
        super().__init__("", current_url=current_url)
        self.pages = pages

    def get(self, url: str) -> None:
        self.visited_urls.append(url)
        payload = self.pages.get(url)
        if payload is None:
            raise AssertionError(f"URL inesperada en test: {url}")
        if isinstance(payload, list):
            if not payload:
                raise AssertionError(f"Sin respuestas restantes para URL en test: {url}")
            payload = payload.pop(0)
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, dict):
            self.page_source = str(payload.get("html", "") or "")
            self.current_url = str(payload.get("current_url", "") or url)
            return
        self.page_source = str(payload or "")
        self.current_url = url


def test_indeed_playwright_extracts_public_cards(tmp_path: Path):
    html = """
    <div class="job_seen_beacon">
      <h2 class="jobTitle"><a href="/rc/clk?jk=3360d1c08d0546d6&utm_source=test"><span>Backend Junior</span></a></h2>
      <span class="companyName">Acme Backend</span>
      <div class="companyLocation">Remoto</div>
      <span class="salary-snippet">$4.000.000</span>
      <span class="date">hace 3 horas</span>
      <div class="job-snippet">Python, SQL y APIs.</div>
    </div>
    """
    driver = _FakeDriver(html)
    scraper = IndeedPlaywrightJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("indeed_playwright", "https://co.indeed.com/jobs?q=backend"))

    assert len(jobs) == 1
    assert jobs[0].title == "Backend Junior"
    assert jobs[0].company == "Acme Backend"
    assert jobs[0].location == "Remoto"
    assert jobs[0].salary == "$4.000.000"
    assert jobs[0].description == "Python, SQL y APIs."
    assert jobs[0].portal == "indeed_playwright"
    assert jobs[0].url == "https://co.indeed.com/viewjob?jk=3360d1c08d0546d6"
    assert driver.quit_called is True


def test_computrabajo_playwright_loads_page(tmp_path: Path):
    source_url = "https://computrabajo.example/jobs"
    html = """
    <article>
      <h2><a href="/ofertas/1">Soporte de Aplicaciones Junior</a></h2>
      <p class="fs16 fc_base mt5">ABC Tecnologia</p>
    </article>
    """
    driver = _FakeDriver(html)
    scraper = ComputrabajoPlaywrightJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    rendered_html = scraper.fetch_search_results(_source("computrabajo_playwright", source_url))

    assert "Soporte de Aplicaciones Junior" in rendered_html
    assert driver.visited_urls == [source_url]
    assert driver.load_states == []
    assert 4.0 in driver.timeout_waits
    assert driver.scrolls == 0
    assert driver.quit_called is True


def test_computrabajo_playwright_extract_jobs_from_rendered_html(tmp_path: Path):
    source_url = "https://computrabajo.example/jobs"
    detail_url = "https://computrabajo.example/ofertas/1"
    driver = _MappedDriver(
        {
            source_url: """
            <article>
              <h2><a href="/ofertas/1?utm_source=test">Backend Junior</a></h2>
              <p class="fs16 fc_base mt5">Acme Backend</p>
              <p class="fs13 fc_aux mt15">Remoto</p>
              <span class="fc_aux fs13">Publicada hoy</span>
              <p class="summary">Python, SQL y APIs.</p>
            </article>
            """,
        }
    )
    scraper = ComputrabajoPlaywrightJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("computrabajo_playwright", source_url))

    assert len(jobs) == 1
    assert jobs[0].title == "Backend Junior"
    assert jobs[0].company == "Acme Backend"
    assert jobs[0].location == "Remoto"
    assert jobs[0].description == "Python, SQL y APIs."
    assert jobs[0].url == f"{detail_url}?utm_source=test"
    assert jobs[0].portal == "computrabajo_playwright"


def test_computrabajo_playwright_extracts_jobs_with_new_selector_fallbacks(tmp_path: Path):
    source_url = "https://computrabajo.example/jobs"
    detail_url = "https://computrabajo.example/oferta-de-trabajo/backend-2"
    driver = _MappedDriver(
        {
            source_url: """
            <html>
              <head><title>Computrabajo Colombia</title></head>
              <body>
                <div class="js-card" data-id="job-2">
                  <div class="ofer-descripcion">
                    <a href="/oferta-de-trabajo/backend-2">Backend Python Junior</a>
                  </div>
                  <span class="empresa">Acme Labs</span>
                  <span class="ubicacion">Bogota</span>
                  <span class="salary-range">$5.000.000</span>
                </div>
              </body>
            </html>
            """,
            detail_url: """
            <div class="box_detail">
              <h1>Backend Python Junior</h1>
              <div class="box_company"><h2>Acme Labs</h2></div>
              <p class="fc_aux">Bogota</p>
              <div class="mbB">Python, APIs y Docker.</div>
            </div>
            """,
        }
    )
    scraper = ComputrabajoPlaywrightJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("computrabajo_playwright", source_url))

    assert len(jobs) == 1
    assert jobs[0].title == "Backend Python Junior"
    assert jobs[0].company == "Acme Labs"
    assert jobs[0].location == "Bogota"
    assert jobs[0].salary == "$5.000.000"
    assert jobs[0].url == detail_url


def test_computrabajo_playwright_logs_debug_when_empty(tmp_path: Path, capsys):
    source_url = "https://computrabajo.example/jobs"
    driver = _FakeDriver(
        """
        <html>
          <head><title>Resultados de busqueda</title></head>
          <body><main><h1>Resultados</h1><p>Contenido sin ofertas detectables.</p></main></body>
        </html>
        """,
        current_url=source_url,
        title="Resultados de busqueda",
    )
    scraper = ComputrabajoPlaywrightJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("computrabajo_playwright", source_url))

    output = capsys.readouterr().out
    assert jobs == []
    assert "[computrabajo] final_url=https://computrabajo.example/jobs" in output
    assert "[computrabajo] html_length=" in output
    assert "[computrabajo] job_containers_before_parse=0" in output
    assert "[computrabajo] blocked=true" in output
    assert "[computrabajo] html_snippet=" in output


def test_computrabajo_playwright_does_not_wait_for_networkidle(tmp_path: Path):
    class _NoNetworkIdleDriver(_FakeDriver):
        def wait_for_load_state(self, state: str = "load", *, timeout: int | None = None) -> bool:
            assert state != "networkidle"
            return super().wait_for_load_state(state, timeout=timeout)

    source_url = "https://computrabajo.example/jobs"
    driver = _NoNetworkIdleDriver(
        """
        <article>
          <h2><a href="/ofertas/1">Soporte de Aplicaciones Junior</a></h2>
          <p class="fs16 fc_base mt5">ABC Tecnologia</p>
        </article>
        """,
        current_url=source_url,
    )
    scraper = ComputrabajoPlaywrightJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    rendered_html = scraper.fetch_search_results(_source("computrabajo_playwright", source_url))

    assert "Soporte de Aplicaciones Junior" in rendered_html
    assert driver.load_states == []


def test_computrabajo_playwright_bypasses_adapter_with_raw_driver(tmp_path: Path, monkeypatch, capsys):
    events: list[str] = []

    class _SentinelRawDriver(_FakeDriver):
        def __init__(self, timeout_seconds: int) -> None:
            super().__init__(
                """
                <article>
                  <h2><a href="/ofertas/1">Soporte de Aplicaciones Junior</a></h2>
                  <p class="fs16 fc_base mt5">ABC Tecnologia</p>
                </article>
                """,
                current_url="https://computrabajo.example/jobs",
            )
            events.append(f"raw:{timeout_seconds}")
            self.last_status_code = 200

    def _unexpected_adapter(*args, **kwargs):
        raise AssertionError("PlaywrightDriverAdapter no debe usarse en Computrabajo")

    monkeypatch.setattr(computrabajo_module, "_RawComputrabajoPlaywrightDriver", _SentinelRawDriver)
    monkeypatch.setattr(playwright_base_module, "PlaywrightDriverAdapter", _unexpected_adapter)

    scraper = ComputrabajoPlaywrightJobScraper(_settings(tmp_path))

    html = scraper.fetch_search_results(_source("computrabajo_playwright", "https://computrabajo.example/jobs"))

    output = capsys.readouterr().out
    assert "Soporte de Aplicaciones Junior" in html
    assert events == ["raw:1"]
    assert "[computrabajo] using_raw_playwright=true" in output
    assert "[computrabajo] adapter_bypassed=true" in output
    assert "[computrabajo] isolated_mode_active=true" in output


def test_linkedin_playwright_extracts_public_cards(tmp_path: Path):
    html = """
    <ul class="jobs-search__results-list">
      <li>
        <div class="base-card base-search-card">
          <h3 class="base-search-card__title">DevOps Trainee</h3>
          <h4 class="base-search-card__subtitle">Acme Cloud</h4>
          <span class="job-search-card__location">Bogota / Remoto</span>
          <time class="job-search-card__listdate">Hace 2 horas</time>
          <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/123?trk=public_jobs">Ver</a>
        </div>
      </li>
    </ul>
    """
    driver = _FakeDriver(html)
    scraper = LinkedInPlaywrightJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_playwright", "https://www.linkedin.com/jobs/search/?f_TPR=r86400"))

    assert len(jobs) == 1
    assert jobs[0].title == "DevOps Trainee"
    assert jobs[0].company == "Acme Cloud"
    assert jobs[0].location == "Bogota / Remoto"
    assert jobs[0].modality == "Remoto"
    assert jobs[0].raw_posted_text == "Hace 2 horas"
    assert jobs[0].portal == "linkedin_playwright"
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/123"
    assert scraper.session_active is False


def test_linkedin_playwright_requests_manual_login_and_reuses_session(tmp_path: Path, monkeypatch):
    source_url = "https://www.linkedin.com/jobs/search/?keywords=backend&f_AL=true&sortBy=DD"
    login_url = "https://www.linkedin.com/login"
    driver = _MappedDriver(
        {
            source_url: [
                {
                    "html": "<html><body><main class='authwall'><h1>You're almost there</h1></main></body></html>",
                    "current_url": "https://www.linkedin.com/authwall",
                },
                {
                    "html": """
                    <main>
                      <div class="job-card-container">
                        <a class="job-card-container__link" href="https://www.linkedin.com/jobs/view/987654321/?currentJobId=987654321&trk=jobs_jserp">
                          <strong>Data Analyst</strong>
                        </a>
                        <div class="artdeco-entity-lockup__subtitle">Data Corp</div>
                        <div class="artdeco-entity-lockup__caption">Colombia - Hibrido</div>
                        <time>Hace 3 horas</time>
                      </div>
                    </main>
                    """,
                    "current_url": source_url,
                },
            ],
            login_url: {
                "html": "<html><body><form><input name='session_key' /></form></body></html>",
                "current_url": login_url,
            },
        }
    )
    monkeypatch.setattr("builtins.input", lambda: "")
    scraper = LinkedInPlaywrightJobScraper(_settings(tmp_path), driver_factory=lambda: driver)
    source = _source("linkedin_playwright", source_url)
    source.interactive_login = True

    jobs = scraper.scrape(source)

    assert len(jobs) == 1
    assert jobs[0].title == "Data Analyst"
    assert jobs[0].portal == "linkedin_playwright"
    assert scraper.session_active is True
    assert scraper.session_mode == "logged_in"
    assert driver.visited_urls == [source_url, login_url, source_url]


def test_playwright_test_cli_builds_linkedin_url_when_url_is_missing(tmp_path: Path, capsys, monkeypatch):
    scraper = Namespace(session_active=True, session_mode="logged_in")
    monkeypatch.setattr(
        cli_module,
        "_run_source_test_with_scraper",
        lambda settings, source: (
            SourceTestResult(
                source=source,
                offers=[
                    ScrapedJob(
                        title="DevOps Trainee",
                        company="Acme Cloud",
                        portal=source.portal,
                        location="Colombia",
                        modality="Remoto",
                        salary="",
                        url="https://www.linkedin.com/jobs/view/123",
                        description="Python y Linux.",
                        requirements="",
                        published_at=None,
                        found_at=datetime.now(UTC),
                        raw_posted_text="Hace 1 hora",
                        source_id=1,
                    )
                ],
                discarded=[],
            ),
            scraper,
        ),
    )

    code = _handle_playwright_test(
        Namespace(
            portal="linkedin",
            url=None,
            keyword="DevOps Trainee",
            location="Colombia",
            date_posted="24h",
            experience_levels=["entry_level"],
            workplace_types=["remote", "hybrid"],
            target_role="devops_trainee",
        ),
        None,
        _settings(tmp_path),
        None,
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "Portal: linkedin_playwright" in output
    assert "Sesion activa: yes" in output
    assert "Modo LinkedIn: logged_in" in output
    assert "keywords=DevOps%20Trainee" in output
    assert "f_TPR=r86400" in output
    assert "f_E=2" in output
    assert "f_WT=2,3" in output
    assert "f_AL=true" in output
    assert "sortBy=DD" in output


def test_playwright_portals_are_registered(tmp_path: Path):
    portals = list_supported_portals()

    assert "linkedin_playwright" in portals
    assert "computrabajo_playwright" in portals
    assert "indeed_playwright" in portals
    assert "linkedin_selenium" not in portals
    assert "indeed_selenium" not in portals
    assert isinstance(get_scraper("linkedin_playwright", _settings(tmp_path)), LinkedInPlaywrightJobScraper)
    assert isinstance(get_scraper("computrabajo", _settings(tmp_path)), ComputrabajoPlaywrightJobScraper)
    assert isinstance(get_scraper("indeed_playwright", _settings(tmp_path)), IndeedPlaywrightJobScraper)
    assert source_uses_persistent_auth("linkedin_playwright") is True


def test_computrabajo_playwright_blocked_page_returns_empty(tmp_path: Path):
    source_url = "https://computrabajo.example/jobs"
    driver = _FakeDriver(
        "<html><head><title>403 Forbidden</title></head><body><main><h1>Forbidden</h1><p>Access Denied</p></main></body></html>",
        current_url=source_url,
    )
    scraper = ComputrabajoPlaywrightJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("computrabajo_playwright", source_url))

    assert jobs == []
    snapshot = scraper.get_last_debug_snapshot()
    assert snapshot is not None
    assert snapshot.block_reason == "computrabajo_403"


def test_playwright_cli_login_subcommand_is_registered():
    parser = cli_module.build_parser()

    login_args = parser.parse_args(["playwright", "login"])

    assert login_args.playwright_command == "login"
    assert login_args.handler == cli_module._handle_playwright_login


def test_playwright_login_opens_login_when_session_does_not_exist(tmp_path: Path, monkeypatch, capsys):
    fake_session = _FakeBrowserSession(has_session=False)
    fake_session.page._closed = True
    monkeypatch.setattr(cli_module, "_build_playwright_linkedin_session", lambda settings: fake_session)

    code = _handle_playwright_login(Namespace(), None, _settings(tmp_path), None)

    output = capsys.readouterr().out
    assert code == 0
    assert fake_session.visited_urls == ["https://www.linkedin.com/login"]
    assert fake_session.saved is True
    assert fake_session.closed is True
    assert "Session already available" not in output
    assert "LinkedIn session saved successfully" in output


def test_playwright_login_opens_feed_when_session_exists(tmp_path: Path, monkeypatch, capsys):
    fake_session = _FakeBrowserSession(has_session=True)
    fake_session.page._closed = True
    monkeypatch.setattr(cli_module, "_build_playwright_linkedin_session", lambda settings: fake_session)

    code = _handle_playwright_login(Namespace(), None, _settings(tmp_path), None)

    output = capsys.readouterr().out
    assert code == 0
    assert fake_session.visited_urls == ["https://www.linkedin.com/feed/"]
    assert fake_session.saved is True
    assert fake_session.closed is True
    assert "Session already available, opening LinkedIn feed instead" in output
    assert "LinkedIn session saved successfully" in output
