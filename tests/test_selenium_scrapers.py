from argparse import Namespace
from pathlib import Path

import pytest

from src.jobops_assistant.cli import _handle_selenium_test
from src.jobops_assistant.models import JobSearchSource
from src.jobops_assistant.scrapers.base_scraper import CaptchaRequiredError, LoginRequiredError, SourceBlockedError
from src.jobops_assistant.scrapers.indeed_selenium_scraper import IndeedSeleniumJobScraper
from src.jobops_assistant.scrapers.linkedin_selenium_scraper import LinkedInSeleniumJobScraper
from src.jobops_assistant.scrapers.registry import get_scraper, list_supported_portals
from src.jobops_assistant.scrapers.selenium_base import SELENIUM_DISABLED_MESSAGE
from src.jobops_assistant.settings import Settings


def _settings(tmp_path: Path, *, enable_selenium: bool = True) -> Settings:
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
        <div class="base-search-card">
          <h3 class="base-search-card__title">DevOps Trainee</h3>
          <h4 class="base-search-card__subtitle">Acme Cloud</h4>
          <span class="job-search-card__location">Bogota / Remoto</span>
          <time>Hace 2 horas</time>
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
    assert jobs[0].portal == "linkedin_selenium"
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/123"


def test_linkedin_selenium_login_required_is_reported(tmp_path: Path):
    driver = _FakeDriver("<html><body><h1>Sign in to view more jobs</h1><p>Join LinkedIn</p></body></html>")
    scraper = LinkedInSeleniumJobScraper(_settings(tmp_path), driver_factory=lambda: driver)

    with pytest.raises(LoginRequiredError):
        scraper.scrape(_source("linkedin_selenium", "https://www.linkedin.com/jobs/search/?keywords=devops"))

    snapshot = scraper.get_last_debug_snapshot()
    assert snapshot is not None
    assert "login" in snapshot.block_reason
    assert driver.quit_called is True


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


def test_selenium_portals_are_registered(tmp_path: Path):
    portals = list_supported_portals()

    assert "indeed_selenium" in portals
    assert "linkedin_selenium" in portals
    assert isinstance(get_scraper("indeed_selenium", _settings(tmp_path)), IndeedSeleniumJobScraper)
    assert isinstance(get_scraper("linkedin_selenium", _settings(tmp_path)), LinkedInSeleniumJobScraper)
