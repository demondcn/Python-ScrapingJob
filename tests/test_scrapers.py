from pathlib import Path

from bs4 import BeautifulSoup
import pytest

from src.jobops_assistant.models import JobSearchSource
from src.jobops_assistant.scrapers.base_scraper import CaptchaRequiredError, SourceBlockedError
from src.jobops_assistant.scrapers.computrabajo_scraper import (
    ComputrabajoJobScraper,
    detect_blocking_state,
    extract_job_cards_robust,
)
from src.jobops_assistant.scrapers.elempleo_scraper import ElempleoJobScraper
from src.jobops_assistant.scrapers.getonboard_scraper import GetOnBoardJobScraper
from src.jobops_assistant.scrapers.indeed_scraper import IndeedJobScraper
from src.jobops_assistant.scrapers.linkedin_scraper import LinkedInJobScraper
from src.jobops_assistant.scrapers.magneto_scraper import MagnetoJobScraper
from src.jobops_assistant.scrapers.sena_scraper import SenaJobScraper
from src.jobops_assistant.scrapers.torre_scraper import TorreJobScraper
from src.jobops_assistant.settings import Settings


def _settings(tmp_path: Path, *, enable_selenium: bool = False) -> Settings:
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
        telegram_digest_max_jobs=10,
        telegram_max_message_chars=3500,
        templates_dir=tmp_path / "templates",
        generated_dir=tmp_path / "generated",
        enable_selenium=enable_selenium,
        selenium_headless=True,
        selenium_page_load_timeout=1,
        selenium_scroll_pause=0,
        selenium_max_scrolls=0,
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
    if scraper_cls is ComputrabajoJobScraper:
        scraper = scraper_cls(
            _settings(tmp_path, enable_selenium=True),
            driver_factory=lambda: _FakeDriver(""),
        )
    else:
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
    def __init__(self, status_code: int, text: str = "", url: str = "", content_type: str = "text/html; charset=utf-8") -> None:
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = {"Content-Type": content_type}

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


class _FakeDriver:
    def __init__(self, html: str = "", *, current_url: str = "https://example.com/jobs") -> None:
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

    def find_elements(self, by, selector: str):
        try:
            return [object() for _ in BeautifulSoup(self.page_source or "", "html.parser").select(selector)]
        except Exception:
            return []


class _MappedDriver(_FakeDriver):
    def __init__(self, pages: dict[str, object], *, current_url: str = "chrome://new-tab-page/") -> None:
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


def test_sena_scraper_fetches_multiple_search_urls(tmp_path: Path):
    url_1 = "https://agenciapublicadeempleo.sena.edu.co/spe-web/spe/public/buscadorVacante?solicitudId=Ingeniero%20de%20software"
    url_2 = "https://agenciapublicadeempleo.sena.edu.co/spe-web/spe/public/buscadorVacante?solicitudId=Programador%20de%20software"
    session = _MappedSession(
        {
            url_1: _FakeResponse(
                200,
                """
                <article class="vacante">
                  <h2><a href="/detalle/101">Ingeniero de Software Junior</a></h2>
                  <div class="company">SENA Uno</div>
                  <div class="location">Bogota</div>
                </article>
                """,
                url=url_1,
            ),
            url_2: _FakeResponse(
                200,
                """
                <article class="vacante">
                  <h2><a href="/detalle/202">Programador de Software</a></h2>
                  <div class="company">SENA Dos</div>
                  <div class="location">Medellin</div>
                </article>
                """,
                url=url_2,
            ),
        }
    )
    scraper = SenaJobScraper(_settings(tmp_path), session=session)
    source = _source("sena")
    source.search_url = f"{url_1}\n{url_2}"

    jobs = scraper.scrape(source)

    assert len(jobs) == 2
    assert {job.title for job in jobs} == {"Ingeniero de Software Junior", "Programador de Software"}
    assert {job.url for job in jobs} == {
        "https://agenciapublicadeempleo.sena.edu.co/detalle/101",
        "https://agenciapublicadeempleo.sena.edu.co/detalle/202",
    }
    assert session.calls == [(url_1, 5), (url_2, 5)]


def test_sena_scraper_uses_fallback_cards_when_dom_changes(tmp_path: Path):
    html = """
    <section class="results">
      <div class="vacante card">
        <h3><a href="/detalle/303">Tecnologo ADSO</a></h3>
        <span class="empresa">SENA Innova</span>
        <span class="ubicacion">Cali</span>
        <p class="description">Analisis, desarrollo y soporte.</p>
      </div>
      <table>
        <tbody>
          <tr class="result-item">
            <td><a href="/detalle/404">Disenador de Soluciones de Software</a></td>
            <td class="empresa">SENA Lab</td>
            <td class="ubicacion">Remoto</td>
          </tr>
        </tbody>
      </table>
    </section>
    """
    scraper = SenaJobScraper(_settings(tmp_path))

    jobs = scraper.parse_search_results(html, _source("sena"))

    assert len(jobs) == 2
    by_title = {job.title: job for job in jobs}
    assert by_title["Tecnologo ADSO"].company == "SENA Innova"
    assert by_title["Tecnologo ADSO"].location == "Cali"
    assert by_title["Disenador de Soluciones de Software"].company == "SENA Lab"
    assert by_title["Disenador de Soluciones de Software"].location == "Remoto"


def test_sena_scraper_extracts_jobs_from_table_text_blocks(tmp_path: Path, caplog):
    html = """
    <table id="buscar-solicitud-public">
      <tbody>
        <tr>
          <td>
            <div class="tdbuscador">
              <div class="container-fluid">
                <div class="row">
                  <div class="span2"><h4>4149888</h4></div>
                  <div class="span5">
                    <h5 class="titulo-color">Ingeniero de software</h5>
                    <h6>Salario no definido</h6>
                    <p>32 meses de experiencia</p>
                    <p>Tipo de contrato: Por obra</p>
                    <p>Bogota, Bogota D.C.</p>
                  </div>
                  <div class="span1">
                    <a class="btn btn-primary" href="/spe-web/spe/demanda/solicitud-sintesis/4149888;jsessionid=ABC123">Postularme</a>
                  </div>
                </div>
              </div>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    """
    scraper = SenaJobScraper(_settings(tmp_path))

    with caplog.at_level("INFO"):
        jobs = scraper.parse_search_results(html, _source("sena"))

    assert len(jobs) == 1
    assert jobs[0].title == "Ingeniero de software"
    assert jobs[0].description.startswith("4149888 Ingeniero de software")
    assert jobs[0].url == "https://sena.example/spe-web/spe/demanda/solicitud-sintesis/4149888"
    assert "[sena] parser_mode=text_fallback" in caplog.text
    assert "[sena] raw_blocks_found=" in caplog.text
    assert "[sena] jobs_detected=1" in caplog.text


def test_sena_scraper_text_fallback_deduplicates_same_title(tmp_path: Path):
    html = """
    <table id="buscar-solicitud-public">
      <tbody>
        <tr>
          <td>
            <div class="tdbuscador">
              <h5>Programador de software</h5>
              <p>Remoto</p>
              <a href="/spe-web/spe/demanda/solicitud-sintesis/111">Postularme</a>
            </div>
          </td>
        </tr>
        <tr>
          <td>
            <div class="tdbuscador">
              <h5>Programador de software</h5>
              <p>Bogota</p>
              <a href="/spe-web/spe/demanda/solicitud-sintesis/222">Postularme</a>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    """
    scraper = SenaJobScraper(_settings(tmp_path))

    jobs = scraper.parse_search_results(html, _source("sena"))

    assert len(jobs) == 1
    assert jobs[0].title == "Programador de software"


def test_scraper_handles_public_block_without_crashing(tmp_path: Path):
    driver = _FakeDriver(
        "<html><body><main><h1>Access denied</h1><p>No autorizado</p></main></body></html>",
        current_url="https://computrabajo.example/blocked",
    )
    scraper = ComputrabajoJobScraper(
        _settings(tmp_path, enable_selenium=True),
        driver_factory=lambda: driver,
    )

    with pytest.raises(SourceBlockedError):
        scraper.fetch_search_results(_source("computrabajo"))

    assert driver.quit_called is True


def test_detect_blocked_403():
    state = detect_blocking_state(
        _FakeResponse(403, "<html><body>Access denied</body></html>"),
        "<html><body>Access denied</body></html>",
        [],
    )

    assert state == "blocked"


def test_detect_scraper_broken_empty_cards():
    html = "<html><body>" + ("<div>contenido visible sin cards</div>" * 80) + "</body></html>"

    state = detect_blocking_state(
        _FakeResponse(200, html, url="https://computrabajo.example/jobs"),
        html,
        [],
    )

    assert state == "scraper_broken"


def test_detect_no_results_valid_page():
    html = """
    <html>
      <body>
        <main>
          <h1>No hay ofertas que coincidan con tu busqueda</h1>
          <p>Prueba con otra palabra clave o una ubicacion distinta.</p>
        </main>
      </body>
    </html>
    """

    state = detect_blocking_state(
        _FakeResponse(200, html, url="https://computrabajo.example/jobs"),
        html,
        [],
    )

    assert state == "no_results"


def test_extract_primary_selector(tmp_path: Path):
    html = """
    <article>
      <h2><a href="/ofertas/1?utm_source=test">Soporte de Aplicaciones Junior</a></h2>
      <p class="fs16 fc_base mt5">ABC Tecnologia</p>
      <p class="fs13 fc_aux mt15">Bogota</p>
    </article>
    """
    scraper = ComputrabajoJobScraper(_settings(tmp_path))
    soup = scraper._soup(html)

    cards = extract_job_cards_robust(html, soup)
    jobs = scraper.parse_search_results(html, _source("computrabajo"))

    assert len(cards) == 1
    assert len(jobs) == 1
    assert jobs[0].title == "Soporte de Aplicaciones Junior"
    assert jobs[0].company == "ABC Tecnologia"


def test_extract_fallback_links(tmp_path: Path):
    html = """
    <div class="layout">
      <div class="whatever-dom-changed">
        <a href="/oferta-de-trabajo/backend-junior-123">Backend Junior</a>
      </div>
    </div>
    """
    scraper = ComputrabajoJobScraper(_settings(tmp_path))

    jobs = scraper.parse_search_results(html, _source("computrabajo"))

    assert len(jobs) == 1
    assert jobs[0].title == "Backend Junior"
    assert jobs[0].url == "https://computrabajo.example/oferta-de-trabajo/backend-junior-123"


def test_no_false_zero_jobs(tmp_path: Path):
    html = """
    <html>
      <body>
        <main>
          <div>{content}</div>
          <section class="dom-cambiado">
            <a href="/oferta-de-trabajo/qa-junior-999">QA Junior</a>
          </section>
        </main>
      </body>
    </html>
    """.format(content="contenido visible " * 150)
    scraper = ComputrabajoJobScraper(_settings(tmp_path))

    state = detect_blocking_state(
        _FakeResponse(200, html, url="https://computrabajo.example/jobs"),
        html,
        [],
    )
    jobs = scraper.parse_search_results(html, _source("computrabajo"))

    assert state == "ok"
    assert len(jobs) == 1
    assert jobs[0].title == "QA Junior"


def test_duplicate_removal(tmp_path: Path):
    html = """
    <div class="results">
      <article>
        <h2><a href="/ofertas/1?utm_source=test">DevOps Junior</a></h2>
      </article>
      <div class="weird-wrapper">
        <a href="/ofertas/1?trackingId=abc">DevOps Junior</a>
      </div>
    </div>
    """
    scraper = ComputrabajoJobScraper(_settings(tmp_path))
    soup = scraper._soup(html)

    cards = extract_job_cards_robust(html, soup)
    jobs = scraper.parse_search_results(html, _source("computrabajo"))

    assert len(cards) == 1
    assert len(jobs) == 1
    assert jobs[0].url == "https://computrabajo.example/ofertas/1?utm_source=test"


def test_selector_fallback_when_dom_changes(tmp_path: Path):
    html = """
    <section class="search-shell">
      <div class="tile">
        <div class="heading">
          <a href="/trabajo-backend-junior-456">Backend Junior</a>
        </div>
        <div class="meta">
          <span>XYZ Tech</span>
          <span>Remoto</span>
        </div>
      </div>
    </section>
    """
    scraper = ComputrabajoJobScraper(_settings(tmp_path))

    jobs = scraper.parse_search_results(html, _source("computrabajo"))

    assert len(jobs) == 1
    assert jobs[0].title == "Backend Junior"
    assert jobs[0].location == "Remoto"


def test_computrabajo_scraper_enriches_jobs_with_public_detail(tmp_path: Path):
    source = _source("computrabajo")
    search_url = source.search_url
    detail_url = "https://computrabajo.example/ofertas/1"
    driver = _MappedDriver(
        {
            search_url: """
                <article>
                  <h2><a href="/ofertas/1?utm_source=test">Desarrollador Junior</a></h2>
                  <p class="fs13 fc_aux mt15">Bogota</p>
                  <span class="fc_aux fs13">Publicada hoy</span>
                </article>
                """,
            detail_url: """
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
        }
    )
    scraper = ComputrabajoJobScraper(
        _settings(tmp_path, enable_selenium=True),
        driver_factory=lambda: driver,
    )

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
    assert driver.visited_urls == [search_url, detail_url]
    assert driver.quit_called is True


def test_computrabajo_scraper_keeps_basic_card_if_detail_fails(tmp_path: Path):
    source = _source("computrabajo")
    search_url = source.search_url
    detail_url = "https://computrabajo.example/ofertas/1"
    driver = _MappedDriver(
        {
            search_url: """
                <article>
                  <h2><a href="/ofertas/1">Desarrollador Junior</a></h2>
                  <p class="fs16 fc_base mt5">Empresa Card</p>
                  <p class="fs13 fc_aux mt15">Bogota</p>
                  <span class="fc_aux fs13">Publicada hoy</span>
                  <p class="mb10">Descripcion basica</p>
                </article>
                """,
            detail_url: RuntimeError("detail timeout"),
        }
    )
    scraper = ComputrabajoJobScraper(
        _settings(tmp_path, enable_selenium=True),
        driver_factory=lambda: driver,
    )

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
    driver = _MappedDriver(
        {
            search_url: """
                <article>
                  <h2><a href="/ofertas/1">Desarrollador Junior</a></h2>
                  <p class="fs16 fc_base mt5">Empresa Card</p>
                </article>
                """,
            detail_url: "<html><body>captcha required</body></html>",
        }
    )
    scraper = ComputrabajoJobScraper(
        _settings(tmp_path, enable_selenium=True),
        driver_factory=lambda: driver,
    )

    jobs = scraper.scrape(source)

    assert len(jobs) == 1
    assert jobs[0].company == "Empresa Card"
    assert jobs[0].description == ""


def test_elempleo_html_with_visible_offers_is_not_marked_as_captcha(tmp_path: Path):
    html = """
    <html>
      <body>
        <script>window.protection = 'captcha token passive';</script>
        <section>
          <h1>Ofertas de Empleo Junior backend publicados hoy</h1>
          <article>
            <h2><a href="/co/ofertas-empleo/backend-junior-1">Backend Junior</a></h2>
            <div class="company">ABC Tecnologia</div>
            <div class="location">Bogotá / Híbrido</div>
            <div class="salary">$4.000.000</div>
            <div class="contract">Término indefinido</div>
            <time>Hoy</time>
            <p class="description">Trabajo con APIs y bases de datos.</p>
          </article>
        </section>
      </body>
    </html>
    """
    scraper = ElempleoJobScraper(_settings(tmp_path))

    jobs = scraper.parse_search_results(html, _source("elempleo"))

    assert len(jobs) == 1
    assert jobs[0].title == "Backend Junior"
    assert jobs[0].company == "ABC Tecnologia"
    assert jobs[0].location == "Bogotá / Híbrido"
    assert jobs[0].modality in {"Híbrido", "HÃ­brido"}
    assert jobs[0].salary == "$4.000.000"
    assert jobs[0].raw_posted_text == "Hoy"


def test_elempleo_cloudflare_turnstile_is_marked_as_block(tmp_path: Path):
    scraper = ElempleoJobScraper(
        _settings(tmp_path),
        session=_FakeSession(
            _FakeResponse(
                200,
                """
                <html>
                  <body>
                    <div class="cf-turnstile"></div>
                    <p>Verify you are human</p>
                  </body>
                </html>
                """,
                url="https://www.elempleo.com/challenge",
            )
        ),
    )

    with pytest.raises(CaptchaRequiredError):
        scraper.fetch_search_results(_source("elempleo"))

    snapshot = scraper.get_last_debug_snapshot()
    assert snapshot is not None
    assert "captcha" in snapshot.block_reason or "turnstile" in snapshot.block_reason


def test_elempleo_scraper_extracts_public_fields_from_simulated_html(tmp_path: Path):
    html = """
    <html>
      <body>
        <article>
          <h2><a href="/co/ofertas-empleo/junior-backend-123">Junior Backend</a></h2>
          <div class="company">XYZ Tech SAS</div>
          <div class="location">Bogotá / Remoto</div>
          <div class="salary">$5.500.000</div>
          <div class="contract">Contrato indefinido</div>
          <time>Hoy</time>
          <p class="description">Desarrollo de APIs REST y soporte a integraciones.</p>
        </article>
      </body>
    </html>
    """
    scraper = ElempleoJobScraper(_settings(tmp_path))

    jobs = scraper.parse_search_results(html, _source("elempleo"))

    assert len(jobs) == 1
    assert jobs[0].title == "Junior Backend"
    assert jobs[0].company == "XYZ Tech SAS"
    assert jobs[0].location == "Bogotá / Remoto"
    assert jobs[0].modality == "Remoto"
    assert jobs[0].url == "https://elempleo.example/co/ofertas-empleo/junior-backend-123"
