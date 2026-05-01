from pathlib import Path

import pytest

from src.jobops_assistant.models import JobSearchSource
from src.jobops_assistant.scrapers.base_scraper import SourceBlockedError
from src.jobops_assistant.scrapers.computrabajo_scraper import ComputrabajoJobScraper
from src.jobops_assistant.scrapers.elempleo_scraper import ElempleoJobScraper
from src.jobops_assistant.scrapers.getonboard_scraper import GetOnBoardJobScraper
from src.jobops_assistant.scrapers.indeed_scraper import IndeedJobScraper
from src.jobops_assistant.scrapers.linkedin_scraper import LinkedInJobScraper
from src.jobops_assistant.scrapers.magneto_scraper import MagnetoJobScraper
from src.jobops_assistant.scrapers.sena_scraper import SenaJobScraper
from src.jobops_assistant.scrapers.torre_scraper import TorreJobScraper
from src.jobops_assistant.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        match_threshold=65,
        telegram_bot_token="",
        telegram_chat_id="",
        gmail_email="",
        gmail_app_password="",
        scraper_timeout=5,
        scraper_user_agent="JobOps Test Agent",
        max_results_per_source=25,
        min_monitor_interval_minutes=10,
        templates_dir=tmp_path / "templates",
        generated_dir=tmp_path / "generated",
    )


def _source(portal: str) -> JobSearchSource:
    source = JobSearchSource(
        portal=portal,
        target_role="soporte_aplicaciones",
        search_url=f"https://{portal}.example/jobs",
        keywords="junior",
        location="Bogota",
        enabled=True,
        interval_minutes=15,
    )
    source.id = 1
    return source


@pytest.mark.parametrize(
    ("scraper_cls", "portal", "html", "title", "company"),
    [
        (
            LinkedInJobScraper,
            "linkedin",
            """
            <ul class="jobs-search__results-list">
              <li>
                <div class="base-search-card">
                  <h3 class="base-search-card__title">DevOps Trainee</h3>
                  <h4 class="base-search-card__subtitle">Acme Cloud</h4>
                  <span class="job-search-card__location">Bogotá / Remoto</span>
                  <time>Hace 2 horas</time>
                  <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/123?trackingId=abc&utm_source=test">Ver</a>
                </div>
              </li>
            </ul>
            """,
            "DevOps Trainee",
            "Acme Cloud",
        ),
        (
            ComputrabajoJobScraper,
            "computrabajo",
            """
            <article>
              <h2><a href="/ofertas/1?utm_source=test">Soporte de Aplicaciones Junior</a></h2>
              <p class="fs16 fc_base mt5">ABC Tecnologia</p>
              <p class="fs13 fc_aux mt15">Bogotá</p>
              <span class="fc_aux fs13">Publicada hoy</span>
            </article>
            """,
            "Soporte de Aplicaciones Junior",
            "ABC Tecnologia",
        ),
        (
            ElempleoJobScraper,
            "elempleo",
            """
            <article>
              <h2><a href="/co/ofertas/2?ref=feed">Infraestructura Junior</a></h2>
              <div class="company">Redes SAS</div>
              <div class="location">Bogotá</div>
              <time>Hoy</time>
            </article>
            """,
            "Infraestructura Junior",
            "Redes SAS",
        ),
        (
            IndeedJobScraper,
            "indeed",
            """
            <div class="job_seen_beacon">
              <h2 class="jobTitle"><a href="/rc/clk?jk=abc&utm_medium=test"><span>Backend Junior</span></a></h2>
              <span class="companyName">Acme Backend</span>
              <div class="companyLocation">Remoto</div>
              <span class="date">hace 3 horas</span>
            </div>
            """,
            "Backend Junior",
            "Acme Backend",
        ),
        (
            MagnetoJobScraper,
            "magneto",
            """
            <article>
              <h2><a href="/vacantes/3?source=test">Cloud Support Junior</a></h2>
              <div class="company">Magneto Labs</div>
              <div class="location">Bogotá</div>
              <time>today</time>
            </article>
            """,
            "Cloud Support Junior",
            "Magneto Labs",
        ),
        (
            TorreJobScraper,
            "torre",
            """
            <article>
              <h2><a href="/jobs/4?trackingId=xyz">QA Junior</a></h2>
              <div class="company">Torre Labs</div>
              <div class="location">Remote</div>
              <time>new</time>
            </article>
            """,
            "QA Junior",
            "Torre Labs",
        ),
        (
            GetOnBoardJobScraper,
            "getonboard",
            """
            <article>
              <h2><a href="/jobs/5?utm_campaign=test">Fullstack Junior</a></h2>
              <div class="company">GoBoard</div>
              <div class="location">Remote</div>
              <time>today</time>
            </article>
            """,
            "Fullstack Junior",
            "GoBoard",
        ),
        (
            SenaJobScraper,
            "sena",
            """
            <article>
              <h2><a href="/vacante/6?refId=tracking">Analista de Soporte</a></h2>
              <div class="company">Servicio Publico de Empleo</div>
              <div class="location">Bogotá</div>
              <time>Publicada hoy</time>
            </article>
            """,
            "Analista de Soporte",
            "Servicio Publico de Empleo",
        ),
    ],
)
def test_scrapers_extract_public_job_cards(tmp_path: Path, monkeypatch, scraper_cls, portal, html, title, company):
    scraper = scraper_cls(_settings(tmp_path))
    source = _source(portal)
    monkeypatch.setattr(scraper, "fetch_search_results", lambda _: html)

    jobs = scraper.scrape(source)

    assert len(jobs) == 1
    assert jobs[0].title == title
    assert jobs[0].company == company
    assert jobs[0].portal == portal
    assert jobs[0].url.startswith(f"https://{portal}.example") or jobs[0].url.startswith("https://www.linkedin.com")
    assert "utm_" not in jobs[0].url
    assert jobs[0].source_id == 1


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.headers: dict[str, str] = {}

    def get(self, url: str, timeout: int):
        return self.response


class _MappedSession:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self.responses = responses
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, int]] = []

    def get(self, url: str, timeout: int):
        self.calls.append((url, timeout))
        response = self.responses.get(url)
        if response is None:
            raise AssertionError(f"URL inesperada en test: {url}")
        return response


@pytest.mark.parametrize("status_code", [403, 429])
def test_scraper_handles_public_block_without_crashing(tmp_path: Path, status_code: int):
    scraper = ComputrabajoJobScraper(_settings(tmp_path), session=_FakeSession(_FakeResponse(status_code)))

    with pytest.raises(SourceBlockedError):
        scraper.fetch_search_results(_source("computrabajo"))


def test_computrabajo_scraper_enriches_jobs_with_public_detail(tmp_path: Path):
    source = _source("computrabajo")
    search_url = source.search_url
    detail_url = "https://computrabajo.example/ofertas/1"
    session = _MappedSession(
        {
            search_url: _FakeResponse(
                200,
                """
                <article>
                  <h2><a href="/ofertas/1?utm_source=test">Desarrollador Junior</a></h2>
                  <p class="fs13 fc_aux mt15">Bogota</p>
                  <span class="fc_aux fs13">Publicada hoy</span>
                </article>
                """,
            ),
            detail_url: _FakeResponse(
                200,
                """
                <div class="box_detail">
                  <h1>Desarrollador Junior</h1>
                  <div class="box_company"><h2>ABC Tecnologia</h2></div>
                  <p class="fc_aux">Bogota / Hibrido</p>
                  <span class="tag base">Hibrido</span>
                  <span class="tag base mb10">$ 3.000.000</span>
                  <time>Publicada hoy</time>
                  <div class="mbB">Desarrollo de aplicaciones web y soporte a incidencias.</div>
                  <div class="mbB">Trabajo con SQL, documentacion tecnica y reportes.</div>
                  <div class="requirements">
                    <ul>
                      <li>Experiencia con SQL y soporte a usuarios.</li>
                      <li>Conocimiento de Git y GitHub.</li>
                    </ul>
                  </div>
                </div>
                """,
            ),
        }
    )
    scraper = ComputrabajoJobScraper(_settings(tmp_path), session=session)

    jobs = scraper.scrape(source)

    assert len(jobs) == 1
    assert jobs[0].title == "Desarrollador Junior"
    assert jobs[0].company == "ABC Tecnologia"
    assert jobs[0].location == "Bogota / Hibrido"
    assert jobs[0].modality.lower().startswith("h")
    assert jobs[0].salary == "$ 3.000.000"
    assert "Desarrollo de aplicaciones web" in jobs[0].description
    assert "Experiencia con SQL" in jobs[0].requirements
    assert jobs[0].raw_posted_text == "Publicada hoy"
    assert session.calls == [(search_url, 5), (detail_url, 5)]


def test_computrabajo_scraper_keeps_basic_card_if_detail_fails(tmp_path: Path):
    source = _source("computrabajo")
    search_url = source.search_url
    detail_url = "https://computrabajo.example/ofertas/1"
    session = _MappedSession(
        {
            search_url: _FakeResponse(
                200,
                """
                <article>
                  <h2><a href="/ofertas/1">Desarrollador Junior</a></h2>
                  <p class="fs16 fc_base mt5">Empresa Card</p>
                  <p class="fs13 fc_aux mt15">Bogota</p>
                  <span class="fc_aux fs13">Publicada hoy</span>
                  <p class="mb10">Descripcion basica</p>
                </article>
                """,
            ),
            detail_url: _FakeResponse(429, ""),
        }
    )
    scraper = ComputrabajoJobScraper(_settings(tmp_path), session=session)

    jobs = scraper.scrape(source)

    assert len(jobs) == 1
    assert jobs[0].title == "Desarrollador Junior"
    assert jobs[0].company == "Empresa Card"
    assert jobs[0].description == "Descripcion basica"
    assert jobs[0].url == detail_url


def test_computrabajo_scraper_detects_captcha_in_detail_without_breaking(tmp_path: Path):
    source = _source("computrabajo")
    search_url = source.search_url
    detail_url = "https://computrabajo.example/ofertas/1"
    session = _MappedSession(
        {
            search_url: _FakeResponse(
                200,
                """
                <article>
                  <h2><a href="/ofertas/1">Desarrollador Junior</a></h2>
                  <p class="fs16 fc_base mt5">Empresa Card</p>
                </article>
                """,
            ),
            detail_url: _FakeResponse(200, "<html><body>captcha required</body></html>"),
        }
    )
    scraper = ComputrabajoJobScraper(_settings(tmp_path), session=session)

    jobs = scraper.scrape(source)

    assert len(jobs) == 1
    assert jobs[0].company == "Empresa Card"
    assert jobs[0].description == ""
