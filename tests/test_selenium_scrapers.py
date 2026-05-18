from argparse import Namespace
from pathlib import Path
import sys
from types import ModuleType

import pytest

from src.jobops_assistant.application_types import EXTERNAL_APPLY, LINKEDIN_EASY_APPLY, UNKNOWN_APPLICATION_TYPE
from src.jobops_assistant import cli as cli_module
from src.jobops_assistant.cli import _handle_selenium_test
from src.jobops_assistant.models import JobSearchSource
from src.jobops_assistant.scrapers.base_scraper import CaptchaRequiredError, LoginRequiredError, SourceBlockedError
from src.jobops_assistant.scrapers.indeed_selenium_scraper import IndeedSeleniumJobScraper
from src.jobops_assistant.scrapers.linkedin_selenium_scraper import (
    LINKEDIN_AUTHWALL_WITHOUT_CARDS_MESSAGE,
    LINKEDIN_BLOCKED_MESSAGE,
    LINKEDIN_EMPTY_RESULTS_MESSAGE,
    LINKEDIN_LOGGED_CARDS_MESSAGE,
    LINKEDIN_LOGGED_EXTRACTION_MESSAGE,
    LINKEDIN_PUBLIC_CARDS_MESSAGE,
    LINKEDIN_SIGNIN_MODAL_CLOSED_MESSAGE,
    LinkedInSeleniumJobScraper,
    build_linkedin_jobs_url,
)
from src.jobops_assistant.scrapers.registry import get_scraper, list_supported_portals
from src.jobops_assistant.scrapers.selenium_base import CHROME_PROFILE_IN_USE_MESSAGE, SELENIUM_DISABLED_MESSAGE
from src.jobops_assistant.settings import Settings


def _settings(
    tmp_path: Path,
    *,
    enable_selenium: bool = True,
    linkedin_fetch_details: bool = False,
    selenium_user_data_dir: str = "",
    selenium_profile_directory: str = "",
) -> Settings:
    return Settings(
        db_path=tmp_path / "selenium.db",
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
        enable_selenium=enable_selenium,
        selenium_headless=True,
        selenium_page_load_timeout=1,
        selenium_scroll_pause=0,
        selenium_max_scrolls=2,
        selenium_user_data_dir=selenium_user_data_dir,
        selenium_profile_directory=selenium_profile_directory,
        linkedin_fetch_details=linkedin_fetch_details,
    )


def _source(portal: str, url: str = "https://example.com/jobs") -> JobSearchSource:
    source = JobSearchSource(
        portal=portal,
        target_role="backend_junior",
        search_url=url,
        keywords="",
        location="",
        enabled=True,
        interval_minutes=30,
    )
    source.id = 1
    return source


class _FakeDriver:
    def __init__(self, html: str, *, current_url: str = "https://example.com/jobs") -> None:
        self.page_source = html
        self.current_url = current_url
        self.timeout = None
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


class _FakeButton:
    def __init__(
        self,
        *,
        text: str = "",
        aria_label: str = "",
        class_name: str = "",
        on_click=None,
    ) -> None:
        self.text = text
        self.aria_label = aria_label
        self.class_name = class_name
        self.on_click = on_click
        self.clicked = False

    def is_displayed(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True

    def get_attribute(self, name: str) -> str:
        if name == "aria-label":
            return self.aria_label
        if name == "class":
            return self.class_name
        return ""

    def click(self) -> None:
        self.clicked = True
        if self.on_click is not None:
            self.on_click()


class _FakeLinkedInModalDriver(_FakeDriver):
    def __init__(self, html: str) -> None:
        super().__init__(html, current_url="https://www.linkedin.com/jobs/search/?keywords=backend")
        self.signin_modal_closed = False
        self.unsafe_buttons = [
            _FakeButton(text="Continuar con Google"),
            _FakeButton(text="Iniciar sesión con el email"),
            _FakeButton(text="Unirse ahora"),
        ]
        self.dismiss_button = _FakeButton(
            text="×",
            on_click=self._close_modal,
        )

    def _close_modal(self) -> None:
        self.signin_modal_closed = True

    def find_elements(self, by, selector: str):
        if selector == "button":
            return [*self.unsafe_buttons, self.dismiss_button]
        return []


class _FakeChromeOptions:
    def __init__(self) -> None:
        self.arguments: list[str] = []

    def add_argument(self, argument: str) -> None:
        self.arguments.append(argument)


def test_indeed_selenium_extracts_public_cards(tmp_path: Path):
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
    scraper = IndeedSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("indeed_selenium", "https://co.indeed.com/jobs?q=backend"))

    assert len(jobs) == 1
    assert jobs[0].title == "Backend Junior"
    assert jobs[0].company == "Acme Backend"
    assert jobs[0].location == "Remoto"
    assert jobs[0].salary == "$4.000.000"
    assert jobs[0].description == "Python, SQL y APIs."
    assert jobs[0].portal == "indeed_selenium"
    assert jobs[0].url == "https://co.indeed.com/viewjob?jk=3360d1c08d0546d6"
    assert driver.timeout == 1
    assert driver.scrolls == 2
    assert driver.quit_called is True


def test_selenium_chrome_options_can_use_regular_profile(tmp_path: Path, monkeypatch, capsys):
    local_app_data = tmp_path / "Local AppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    chrome_user_data = local_app_data / "Google" / "Chrome" / "User Data"
    scraper = LinkedInSeleniumJobScraper(
        _settings(
            tmp_path,
            selenium_user_data_dir=r"%LOCALAPPDATA%\Google\Chrome\User Data",
            selenium_profile_directory="Default",
        )
    )
    options = _FakeChromeOptions()

    scraper._apply_chrome_options(options)

    assert f"--user-data-dir={chrome_user_data}" in options.arguments
    assert "--profile-directory=Default" in options.arguments
    assert "--no-first-run" in options.arguments
    assert "--no-default-browser-check" in options.arguments
    assert "--disable-session-crashed-bubble" in options.arguments
    assert "--disable-infobars" in options.arguments
    assert "--incognito" not in options.arguments
    output = capsys.readouterr().out
    assert f"Selenium: usando user-data-dir={chrome_user_data}" in output
    assert "Selenium: usando profile-directory=Default" in output


def test_selenium_scraper_with_profile_navigates_to_source_url(tmp_path: Path, capsys):
    html = """
    <div class="base-card base-search-card">
      <h3 class="base-search-card__title">Junior Backend</h3>
      <h4 class="base-search-card__subtitle">Acme API</h4>
      <span class="job-search-card__location">Colombia</span>
      <time class="job-search-card__listdate">Hace 1 hora</time>
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/456?trk=public_jobs">Ver</a>
    </div>
    """
    source_url = "https://www.linkedin.com/jobs/search/?keywords=Junior%20Backend&location=Colombia&f_TPR=r10800"
    driver = _FakeDriver(html, current_url="chrome://new-tab-page/")
    scraper = LinkedInSeleniumJobScraper(
        _settings(
            tmp_path,
            selenium_user_data_dir=r"C:\Users\demo\AppData\Local\Google\Chrome\User Data",
            selenium_profile_directory="Default",
        ),
        driver_factory=lambda: driver,
    )

    jobs = scraper.scrape(_source("linkedin_selenium", source_url))

    output = capsys.readouterr().out
    assert driver.visited_urls == [source_url]
    assert f"Selenium: navegando a URL: {source_url}" in output
    assert f"Selenium: current_url después de driver.get: {source_url}" in output
    assert len(jobs) == 1


def test_selenium_profile_in_use_error_has_clear_message(tmp_path: Path, monkeypatch):
    def _raise_profile_in_use(*args, **kwargs):
        raise RuntimeError("session not created: probably user data directory is already in use")

    selenium_module = ModuleType("selenium")
    webdriver_module = ModuleType("selenium.webdriver")
    chrome_module = ModuleType("selenium.webdriver.chrome")
    options_module = ModuleType("selenium.webdriver.chrome.options")
    webdriver_module.Chrome = _raise_profile_in_use
    options_module.Options = _FakeChromeOptions
    selenium_module.webdriver = webdriver_module
    chrome_module.options = options_module

    monkeypatch.setitem(sys.modules, "selenium", selenium_module)
    monkeypatch.setitem(sys.modules, "selenium.webdriver", webdriver_module)
    monkeypatch.setitem(sys.modules, "selenium.webdriver.chrome", chrome_module)
    monkeypatch.setitem(sys.modules, "selenium.webdriver.chrome.options", options_module)

    scraper = LinkedInSeleniumJobScraper(
        _settings(
            tmp_path,
            selenium_user_data_dir=r"C:\Users\demo\AppData\Local\Google\Chrome\User Data",
            selenium_profile_directory="Default",
        )
    )

    with pytest.raises(SourceBlockedError) as exc:
        scraper._build_driver()

    assert str(exc.value) == CHROME_PROFILE_IN_USE_MESSAGE


def test_linkedin_cli_subcommands_are_registered():
    parser = cli_module.build_parser()

    login_args = parser.parse_args(["linkedin", "login-profile"])
    info_args = parser.parse_args(["linkedin", "profile-info"])

    assert login_args.linkedin_command == "login-profile"
    assert login_args.handler == cli_module._handle_linkedin_login_profile
    assert info_args.linkedin_command == "profile-info"
    assert info_args.handler == cli_module._handle_linkedin_profile_info


def test_linkedin_login_profile_opens_linkedin_with_configured_profile(tmp_path: Path, capsys, monkeypatch):
    driver = _FakeDriver("", current_url="chrome://new-tab-page/")
    monkeypatch.setattr(cli_module, "_build_linkedin_profile_driver", lambda settings: driver)

    code = cli_module._handle_linkedin_login_profile(
        Namespace(),
        None,
        _settings(
            tmp_path,
            selenium_user_data_dir=r"C:\Users\demo\AppData\Local\Google\Chrome\User Data",
            selenium_profile_directory="Default",
        ),
        None,
    )

    output = capsys.readouterr().out
    assert code == 0
    assert output == f"{cli_module.LINKEDIN_LOGIN_PROFILE_MESSAGE}\n"
    assert driver.visited_urls == [cli_module.LINKEDIN_HOME_URL]
    assert driver.quit_called is True


def test_linkedin_profile_info_prints_configured_profile(tmp_path: Path, capsys, monkeypatch):
    local_app_data = tmp_path / "Local AppData"
    profile_dir = local_app_data / "Google" / "Chrome" / "User Data" / "Default"
    profile_dir.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    settings = _settings(
        tmp_path,
        selenium_user_data_dir=r"%LOCALAPPDATA%\Google\Chrome\User Data",
        selenium_profile_directory="Default",
    )

    code = cli_module._handle_linkedin_profile_info(Namespace(), None, settings, None)

    output = capsys.readouterr().out
    assert code == 0
    assert f"JOBOPS_SELENIUM_USER_DATA_DIR: {profile_dir.parent}" in output
    assert "JOBOPS_SELENIUM_PROFILE_DIRECTORY: Default" in output
    assert "carpeta existe: True" in output
    assert "JOBOPS_SELENIUM_HEADLESS: True" in output


def test_indeed_selenium_security_check_is_reported_as_block(tmp_path: Path):
    driver = _FakeDriver("<html><body><h1>Security Check</h1><p>captcha required</p></body></html>")
    scraper = IndeedSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    with pytest.raises(CaptchaRequiredError):
        scraper.scrape(_source("indeed_selenium", "https://co.indeed.com/jobs?q=backend"))

    snapshot = scraper.get_last_debug_snapshot()
    assert snapshot is not None
    assert snapshot.block_reason in {"security check", "captcha"}
    assert driver.quit_called is True


def test_linkedin_selenium_extracts_public_cards(tmp_path: Path):
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
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?f_TPR=r86400"))

    assert len(jobs) == 1
    assert jobs[0].title == "DevOps Trainee"
    assert jobs[0].company == "Acme Cloud"
    assert jobs[0].location == "Bogota / Remoto"
    assert jobs[0].modality == "Remoto"
    assert jobs[0].raw_posted_text == "Hace 2 horas"
    assert jobs[0].portal == "linkedin_selenium"
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/123"


def test_linkedin_selenium_detects_spanish_easy_apply_from_card(tmp_path: Path):
    html = """
    <div class="base-card base-search-card">
      <h3 class="base-search-card__title">Backend Junior</h3>
      <h4 class="base-search-card__subtitle">Acme API</h4>
      <span class="job-search-card__location">Colombia</span>
      <span>Solicitud sencilla</span>
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/124?trk=public_jobs">Ver</a>
    </div>
    """
    driver = _FakeDriver(html)
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=backend"))

    assert len(jobs) == 1
    assert jobs[0].application_type == LINKEDIN_EASY_APPLY


def test_linkedin_selenium_detects_english_easy_apply_from_card(tmp_path: Path):
    html = """
    <div class="base-card base-search-card">
      <h3 class="base-search-card__title">Backend Junior</h3>
      <h4 class="base-search-card__subtitle">Acme API</h4>
      <span class="job-search-card__location">Colombia</span>
      <button aria-label="Easy Apply to Backend Junior">Easy Apply</button>
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/125?trk=public_jobs">Ver</a>
    </div>
    """
    driver = _FakeDriver(html)
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=backend"))

    assert len(jobs) == 1
    assert jobs[0].application_type == LINKEDIN_EASY_APPLY


def test_linkedin_selenium_detects_external_apply_from_company_website_button(tmp_path: Path):
    html = """
    <div class="base-card base-search-card">
      <h3 class="base-search-card__title">Backend Junior</h3>
      <h4 class="base-search-card__subtitle">Acme API</h4>
      <span class="job-search-card__location">Colombia</span>
      <button>Solicitar en el sitio web de la empresa</button>
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/126?trk=public_jobs">Ver</a>
    </div>
    """
    driver = _FakeDriver(html)
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=backend"))

    assert len(jobs) == 1
    assert jobs[0].application_type == EXTERNAL_APPLY


def test_linkedin_selenium_marks_application_type_unknown_without_apply_signal(tmp_path: Path):
    html = """
    <div class="base-card base-search-card">
      <h3 class="base-search-card__title">Backend Junior</h3>
      <h4 class="base-search-card__subtitle">Acme API</h4>
      <span class="job-search-card__location">Colombia</span>
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/127?trk=public_jobs">Ver</a>
    </div>
    """
    driver = _FakeDriver(html)
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=backend"))

    assert len(jobs) == 1
    assert jobs[0].application_type == UNKNOWN_APPLICATION_TYPE


def test_linkedin_logged_in_list_item_extracts_job(tmp_path: Path, capsys):
    html = """
    <div class="global-nav">Inicio Empleos Mensajes Notificaciones Yo</div>
    <div class="scaffold-layout__list-container">
      <ul>
        <li class="jobs-search-results__list-item">
          <div class="job-card-container">
            <a class="job-card-container__link" href="/jobs/view/4410302233/?refId=abc&trackingId=xyz">
              <strong>Junior Backend Developer</strong>
            </a>
            <span class="job-card-container__primary-description">Acme API</span>
            <ul class="job-card-container__metadata-wrapper">
              <li class="job-card-container__metadata-item">Bogota, Colombia (Remoto)</li>
            </ul>
            <time datetime="2026-05-12">Hace 1 hora</time>
            <div class="job-card-container__job-insight-text">Python, APIs y SQL.</div>
          </div>
        </li>
      </ul>
    </div>
    """
    driver = _FakeDriver(html, current_url="https://www.linkedin.com/jobs/search/?keywords=backend")
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=backend"))

    output = capsys.readouterr().out
    assert LINKEDIN_LOGGED_CARDS_MESSAGE in output
    assert LINKEDIN_LOGGED_EXTRACTION_MESSAGE in output
    assert len(jobs) == 1
    assert jobs[0].title == "Junior Backend Developer"
    assert jobs[0].company == "Acme API"
    assert jobs[0].location == "Bogota, Colombia (Remoto)"
    assert jobs[0].modality == "Remoto"
    assert jobs[0].description == "Python, APIs y SQL."
    assert jobs[0].raw_posted_text == "Hace 1 hora"
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4410302233"


def test_linkedin_logged_in_job_card_container_extracts_job(tmp_path: Path, capsys):
    html = """
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
    """
    driver = _FakeDriver(html, current_url="https://www.linkedin.com/jobs/search/?keywords=data")
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=data"))

    output = capsys.readouterr().out
    assert LINKEDIN_LOGGED_CARDS_MESSAGE in output
    assert len(jobs) == 1
    assert jobs[0].title == "Data Analyst"
    assert jobs[0].company == "Data Corp"
    assert jobs[0].location == "Colombia - Hibrido"
    assert jobs[0].modality in {"Híbrido", "HÃ­brido"}
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/987654321"


def test_linkedin_logged_in_nav_with_cards_does_not_block(tmp_path: Path, capsys):
    html = """
    <header>
      <nav>Inicio Mi red Empleos Mensajes Notificaciones Yo</nav>
      <a>Sign in</a>
      <a>Join now</a>
    </header>
    <div class="scaffold-layout__list-container">
      <li class="jobs-search-results__list-item">
        <a class="job-card-list__title" href="/jobs/view/555/"><strong>QA Tester Bilingue</strong></a>
        <span class="job-card-container__primary-description">Quality Co</span>
        <li class="job-card-container__metadata-item">Remote</li>
      </li>
    </div>
    """
    driver = _FakeDriver(html, current_url="https://www.linkedin.com/jobs/search/?keywords=qa")
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=qa"))

    output = capsys.readouterr().out
    assert LINKEDIN_LOGGED_CARDS_MESSAGE in output
    assert LINKEDIN_AUTHWALL_WITHOUT_CARDS_MESSAGE not in output
    assert len(jobs) == 1
    assert jobs[0].title == "QA Tester Bilingue"


def test_linkedin_logged_in_empty_results_returns_empty_list(tmp_path: Path, capsys):
    html = """
    <html>
      <body>
        <div class="global-nav">Inicio Empleos Mensajes</div>
        <main>
          <h2>No se han encontrado coincidencias</h2>
          <p>Comprueba que las palabras clave estén bien escritas.</p>
        </main>
      </body>
    </html>
    """
    driver = _FakeDriver(html, current_url="https://www.linkedin.com/jobs/search/?keywords=zzzz")
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=zzzz"))

    output = capsys.readouterr().out
    assert LINKEDIN_EMPTY_RESULTS_MESSAGE in output
    assert jobs == []


def test_linkedin_checkpoint_without_logged_in_cards_blocks(tmp_path: Path):
    html = """
    <html>
      <body>
        <form action="/checkpoint/challenge">
          <h1>Security Verification</h1>
          <p>captcha required</p>
        </form>
      </body>
    </html>
    """
    driver = _FakeDriver(html, current_url="https://www.linkedin.com/checkpoint/challenge")
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    with pytest.raises(CaptchaRequiredError):
        scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=backend"))

    snapshot = scraper.get_last_debug_snapshot()
    assert snapshot is not None
    assert snapshot.block_reason in {"security verification", "captcha", "checkpoint"}


def test_linkedin_login_modal_with_public_cards_does_not_block(tmp_path: Path, capsys):
    html = """
    <div class="sign-in-modal">
      <h2>Inicia sesión para ver más empleos</h2>
      <button>Continuar con Google</button>
      <button>Iniciar sesión con el email</button>
      <button>Unirse ahora</button>
    </div>
    <div class="base-card base-search-card">
      <h3 class="base-search-card__title">Backend Junior</h3>
      <h4 class="base-search-card__subtitle">Acme Backend</h4>
      <span class="job-search-card__location">Colombia</span>
      <time class="job-search-card__listdate">Hace 1 hora</time>
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/789?trk=public_jobs">Ver</a>
    </div>
    <div class="description__text">APIs, Python y SQL.</div>
    """
    driver = _FakeDriver(html)
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=backend"))

    output = capsys.readouterr().out
    assert LINKEDIN_PUBLIC_CARDS_MESSAGE in output
    assert len(jobs) == 1
    assert jobs[0].title == "Backend Junior"
    assert jobs[0].company == "Acme Backend"
    assert jobs[0].description == "APIs, Python y SQL."


def test_linkedin_header_login_text_with_public_cards_does_not_block(tmp_path: Path, capsys):
    html = """
    <header>
      <a>Iniciar sesión</a>
      <a>Unirse ahora</a>
      <a>Sign in</a>
      <a>Join now</a>
      <a href="/checkpoint/challenge">Checkpoint help link</a>
    </header>
    <ul class="jobs-search__results-list">
      <li>
        <h3 class="base-search-card__title">Junior Software Developer - Remote</h3>
        <h4 class="base-search-card__subtitle">Acme Remote</h4>
        <span class="job-search-card__location">Remote</span>
        <time class="job-search-card__listdate">Hace 2 horas</time>
        <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/791?trk=public_jobs">Ver</a>
      </li>
    </ul>
    """
    driver = _FakeDriver(html)
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=backend"))

    output = capsys.readouterr().out
    assert LINKEDIN_PUBLIC_CARDS_MESSAGE in output
    assert len(jobs) == 1
    assert jobs[0].title == "Junior Software Developer - Remote"
    assert jobs[0].company == "Acme Remote"
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/791"


def test_linkedin_spanish_empty_results_returns_empty_list(tmp_path: Path, capsys):
    html = """
    <html>
      <body>
        <header>
          <a>Iniciar sesión</a>
          <a>Unirse ahora</a>
        </header>
        <main>
          <h1>No se han encontrado coincidencias para Empleos de Junior Backend en Colombia</h1>
          <p>Comprueba que las palabras clave estén bien escritas.</p>
        </main>
      </body>
    </html>
    """
    driver = _FakeDriver(html)
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=backend"))

    output = capsys.readouterr().out
    assert LINKEDIN_EMPTY_RESULTS_MESSAGE in output
    assert jobs == []
    assert driver.quit_called is True


def test_linkedin_english_empty_results_returns_empty_list(tmp_path: Path, capsys):
    html = """
    <html>
      <body>
        <main>
          <h1>No results found</h1>
          <p>No matching jobs found for this search.</p>
        </main>
      </body>
    </html>
    """
    driver = _FakeDriver(html)
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=backend"))

    output = capsys.readouterr().out
    assert LINKEDIN_EMPTY_RESULTS_MESSAGE in output
    assert jobs == []
    assert driver.quit_called is True


def test_linkedin_closes_public_signin_modal_and_extracts_cards(tmp_path: Path, capsys):
    html = """
    <div class="modal">
      <button aria-label="Dismiss" class="modal__dismiss">×</button>
      <button>Continuar con Google</button>
      <button>Iniciar sesión con el email</button>
      <button>Unirse ahora</button>
    </div>
    <div class="base-card base-search-card">
      <h3 class="base-search-card__title">Junior Backend</h3>
      <h4 class="base-search-card__subtitle">Acme API</h4>
      <span class="job-search-card__location">Remoto</span>
      <time class="job-search-card__listdate">Hace 30 minutos</time>
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/790?trk=public_jobs">Ver</a>
    </div>
    """
    driver = _FakeLinkedInModalDriver(html)
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=backend"))

    output = capsys.readouterr().out
    assert LINKEDIN_SIGNIN_MODAL_CLOSED_MESSAGE in output
    assert LINKEDIN_PUBLIC_CARDS_MESSAGE in output
    assert driver.signin_modal_closed is True
    assert driver.dismiss_button.clicked is True
    assert all(not button.clicked for button in driver.unsafe_buttons)
    assert len(jobs) == 1
    assert jobs[0].title == "Junior Backend"
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/790"


def test_linkedin_selenium_login_required_is_reported(tmp_path: Path):
    driver = _FakeDriver("<html><body><h1>Sign in to view more jobs</h1><p>Join LinkedIn</p></body></html>")
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    with pytest.raises(LoginRequiredError):
        scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=devops"))

    snapshot = scraper.get_last_debug_snapshot()
    assert snapshot is not None
    assert "login" in snapshot.block_reason
    assert scraper.login_error_message == LINKEDIN_BLOCKED_MESSAGE
    assert driver.quit_called is True


def test_linkedin_authwall_without_public_cards_is_reported(tmp_path: Path, capsys):
    html = """
    <html>
      <body>
        <main class="authwall">
          <h1>You're almost there</h1>
          <p>Join LinkedIn to view more jobs.</p>
        </main>
      </body>
    </html>
    """
    driver = _FakeDriver(html)
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    with pytest.raises(LoginRequiredError) as exc:
        scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=devops"))

    output = capsys.readouterr().out
    assert str(exc.value) == LINKEDIN_BLOCKED_MESSAGE
    assert LINKEDIN_AUTHWALL_WITHOUT_CARDS_MESSAGE in output
    snapshot = scraper.get_last_debug_snapshot()
    assert snapshot is not None
    assert snapshot.block_reason == "authwall"
    assert driver.quit_called is True


def test_linkedin_selenium_captcha_is_reported(tmp_path: Path):
    driver = _FakeDriver("<html><body><h1>Security Verification</h1><p>captcha required</p></body></html>")
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    with pytest.raises(CaptchaRequiredError) as exc:
        scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=devops"))

    assert str(exc.value) == LINKEDIN_BLOCKED_MESSAGE
    snapshot = scraper.get_last_debug_snapshot()
    assert snapshot is not None
    assert snapshot.block_reason in {"security verification", "captcha"}
    assert driver.quit_called is True


def test_linkedin_jobs_url_builder_adds_supported_filters():
    url = build_linkedin_jobs_url(
        "DevOps Trainee",
        "Colombia",
        date_posted="24h",
        experience_levels=["entry_level"],
        workplace_types=["remote", "hybrid"],
    )

    assert url.startswith("https://www.linkedin.com/jobs/search/?")
    assert "keywords=DevOps%20Trainee" in url
    assert "location=Colombia" in url
    assert "f_TPR=r86400" in url
    assert "f_E=2" in url
    assert "f_WT=2,3" in url


def test_linkedin_detail_block_keeps_basic_card(tmp_path: Path):
    search_html = """
    <div class="base-card">
      <h3 class="base-search-card__title">Junior Backend</h3>
      <h4 class="base-search-card__subtitle">Acme API</h4>
      <span class="job-search-card__location">Colombia</span>
      <time class="job-search-card__listdate">Hace 1 hora</time>
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/456?trk=public_jobs">Ver</a>
    </div>
    """
    detail_html = "<html><body><main class='authwall'>Join LinkedIn to view this job</main></body></html>"
    drivers = iter([_FakeDriver(search_html), _FakeDriver(detail_html)])
    scraper = LinkedInSeleniumJobScraper(
        _settings(tmp_path, linkedin_fetch_details=True),
        driver_factory=lambda: next(drivers),
    )

    jobs = scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=backend"))

    assert len(jobs) == 1
    assert jobs[0].title == "Junior Backend"
    assert jobs[0].company == "Acme API"
    assert jobs[0].description == ""


def test_selenium_scraper_requires_enable_flag(tmp_path: Path):
    calls = 0

    def _driver_factory():
        nonlocal calls
        calls += 1
        return _FakeDriver("")

    scraper = IndeedSeleniumJobScraper(_settings(tmp_path, enable_selenium=False), driver_factory=_driver_factory)

    with pytest.raises(SourceBlockedError) as exc:
        scraper.scrape(_source("indeed_selenium"))

    assert str(exc.value) == SELENIUM_DISABLED_MESSAGE
    assert calls == 0


def test_selenium_test_cli_reports_disabled_flag(tmp_path: Path, capsys):
    code = _handle_selenium_test(
        Namespace(portal="indeed", url="https://co.indeed.com/jobs?q=backend", target_role="backend_junior"),
        None,
        _settings(tmp_path, enable_selenium=False),
        None,
    )

    output = capsys.readouterr().out
    assert code == 1
    assert SELENIUM_DISABLED_MESSAGE in output
    assert "Portal: indeed_selenium" in output


def test_selenium_test_cli_builds_linkedin_url_when_url_is_missing(tmp_path: Path, capsys):
    code = _handle_selenium_test(
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
        _settings(tmp_path, enable_selenium=False),
        None,
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "Portal: linkedin_selenium" in output
    assert "keywords=DevOps%20Trainee" in output
    assert "f_TPR=r86400" in output
    assert "f_E=2" in output
    assert "f_WT=2,3" in output


def test_selenium_test_cli_prints_linkedin_offers_when_cards_are_available(tmp_path: Path, capsys, monkeypatch):
    def _fake_test_source(settings, source):
        assert source.portal == "linkedin_selenium"
        assert source.search_url == "https://www.linkedin.com/jobs/search/?keywords=backend"
        return Namespace(
            error="",
            offers=[
                Namespace(
                    title="Junior Software Engineer - Remote",
                    company="Acme Remote",
                    location="Remote",
                    url="https://www.linkedin.com/jobs/view/792",
                    description="Python y APIs.",
                    requirements="",
                )
            ],
            discarded=[],
        )

    monkeypatch.setattr(cli_module, "test_source", _fake_test_source)

    code = _handle_selenium_test(
        Namespace(
            portal="linkedin",
            url="https://www.linkedin.com/jobs/search/?keywords=backend",
            target_role="backend_junior",
        ),
        None,
        _settings(tmp_path),
        None,
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "Ofertas detectadas: 1" in output
    assert "Junior Software Engineer - Remote | Acme Remote | Remote | https://www.linkedin.com/jobs/view/792" in output


def test_selenium_test_cli_reports_zero_linkedin_offers_without_error(tmp_path: Path, capsys, monkeypatch):
    def _fake_test_source(settings, source):
        assert source.portal == "linkedin_selenium"
        return Namespace(error="", offers=[], discarded=[])

    monkeypatch.setattr(cli_module, "test_source", _fake_test_source)

    code = _handle_selenium_test(
        Namespace(
            portal="linkedin",
            url="https://www.linkedin.com/jobs/search/?keywords=backend",
            target_role="backend_junior",
        ),
        None,
        _settings(tmp_path),
        None,
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "Portal: linkedin_selenium" in output
    assert "Ofertas detectadas: 0" in output
    assert "Error:" not in output


def test_selenium_portals_are_registered(tmp_path: Path):
    portals = list_supported_portals()

    assert "indeed_selenium" in portals
    assert "linkedin_selenium" in portals
    assert isinstance(get_scraper("indeed_selenium", _settings(tmp_path)), IndeedSeleniumJobScraper)
    assert isinstance(get_scraper("linkedin_selenium", _settings(tmp_path)), LinkedInSeleniumJobScraper)
