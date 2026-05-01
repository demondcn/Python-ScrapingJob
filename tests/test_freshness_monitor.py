from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from src.jobops_assistant.database import create_session_factory, create_sqlite_engine, init_db
from src.jobops_assistant.freshness_monitor import is_scraped_job_fresh, run_fresh_monitor
from src.jobops_assistant.job_service import list_offers
from src.jobops_assistant.models import JobOffer
from src.jobops_assistant.profile_service import upsert_profile
from src.jobops_assistant.scrapers.base_scraper import ScrapedJob, SourceBlockedError
from src.jobops_assistant.search_sources import add_source, get_due_sources, get_source_by_id
from src.jobops_assistant.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "monitor.db",
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


class _FakeScraper:
    def __init__(self, jobs=None, error: Exception | None = None) -> None:
        self.jobs = jobs or []
        self.error = error

    def scrape(self, source):
        if self.error is not None:
            raise self.error
        return self.jobs


def test_monitor_saves_new_offers_dedupes_and_notifies_once(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)
    calls: list[tuple[int, str | None]] = []

    with Session(session_factory.kw["bind"]) as session:
        upsert_profile(
            session,
            full_name="Cris Perez",
            email="cris@example.com",
            phone="3000000000",
            city="Bogota",
            summary="Interes en soporte de aplicaciones",
            skills="SQL,Git,Documentacion",
            projects="",
            education="Tecnologo",
            target_roles="soporte_aplicaciones",
        )
        source = add_source(
            session,
            portal="computrabajo",
            target_role="soporte_aplicaciones",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        job = ScrapedJob(
            title="Soporte de Aplicaciones Junior",
            company="ABC Tecnologia",
            portal="computrabajo",
            location="Bogotá",
            modality="Híbrido",
            salary="",
            url="https://computrabajo.example/jobs/123?utm_source=test",
            description="Soporte a usuarios, SQL, aplicaciones web y tickets.",
            requirements="Perfil junior con documentacion tecnica.",
            published_at=datetime.now(UTC),
            found_at=datetime.now(UTC),
            raw_posted_text="Publicada hoy",
            source_id=source.id,
        )

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper([job]),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert",
            lambda settings, offer, target_role=None: calls.append((offer.id, target_role)) or (True, "Alerta enviada por Telegram."),
        )

        first_logs = run_fresh_monitor(session, settings, force_all=True)
        second_logs = run_fresh_monitor(session, settings, force_all=True)

        offers = list_offers(session)
        assert len(offers) == 1
        assert offers[0].title == "Soporte de Aplicaciones Junior"
        assert calls == [(offers[0].id, "soporte_aplicaciones")]
        assert any("nuevas=1" in line for line in first_logs)
        assert any("duplicados=1" in line for line in second_logs)


def test_monitor_continues_if_one_portal_fails(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        source_a = add_source(
            session,
            portal="linkedin",
            target_role="devops_trainee",
            search_url="https://linkedin.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        source_b = add_source(
            session,
            portal="torre",
            target_role="devops_trainee",
            search_url="https://torre.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        good_job = ScrapedJob(
            title="DevOps Trainee",
            company="Torre Labs",
            portal="torre",
            location="Remote",
            modality="Remoto",
            salary="",
            url="https://torre.example/jobs/1",
            description="Junior DevOps con Linux, Docker y cloud.",
            requirements="Trainee",
            published_at=datetime.now(UTC),
            found_at=datetime.now(UTC),
            raw_posted_text="today",
            source_id=source_b.id,
        )

        def fake_get_scraper(portal, settings):
            if portal == "linkedin":
                return _FakeScraper(error=SourceBlockedError("LinkedIn no permitió leer resultados públicos. Se omite esta fuente."))
            return _FakeScraper([good_job])

        monkeypatch.setattr("src.jobops_assistant.freshness_monitor.get_scraper", fake_get_scraper)
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert",
            lambda settings, offer, target_role=None: (True, "Alerta enviada por Telegram."),
        )

        logs = run_fresh_monitor(session, settings, force_all=True)

        offers = list_offers(session)
        failed_source = get_source_by_id(session, source_a.id)
        ok_source = get_source_by_id(session, source_b.id)
        assert len(offers) == 1
        assert offers[0].portal == "torre"
        assert any("linkedin" in line.lower() and "error" in line.lower() for line in logs)
        assert any("torre" in line.lower() and "nuevas=1" in line.lower() for line in logs)
        assert failed_source is not None and "LinkedIn no permitió leer resultados públicos" in failed_source.last_error
        assert ok_source is not None and ok_source.last_error == ""


def test_monitor_calls_telegram_only_above_threshold(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)
    calls: list[int] = []

    with Session(session_factory.kw["bind"]) as session:
        add_source(
            session,
            portal="computrabajo",
            target_role="soporte_aplicaciones",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        jobs = [
            ScrapedJob(
                title="Soporte de Aplicaciones Junior",
                company="ABC Tecnologia",
                portal="computrabajo",
                location="Bogotá",
                modality="Híbrido",
                salary="",
                url="https://computrabajo.example/jobs/100",
                description="Soporte a usuarios, SQL, tickets y aplicaciones web.",
                requirements="Junior",
                published_at=datetime.now(UTC),
                found_at=datetime.now(UTC),
                raw_posted_text="Publicada hoy",
                source_id=1,
            ),
            ScrapedJob(
                title="Practicante Administrativo",
                company="BackOffice",
                portal="computrabajo",
                location="Bogotá",
                modality="Presencial",
                salary="",
                url="https://computrabajo.example/jobs/101",
                description="Archivo y tareas administrativas.",
                requirements="Sin experiencia",
                published_at=datetime.now(UTC),
                found_at=datetime.now(UTC),
                raw_posted_text="Publicada hoy",
                source_id=1,
            ),
        ]

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper(jobs),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert",
            lambda settings, offer, target_role=None: calls.append(offer.id) or (True, "Alerta enviada por Telegram."),
        )

        run_fresh_monitor(session, settings, force_all=True)

        offers = list_offers(session)
        assert len(offers) == 2
        assert len(calls) == 1


def test_freshness_detection_supports_relative_and_absolute_dates():
    fresh_job = ScrapedJob(
        title="Soporte",
        company="ACME",
        portal="computrabajo",
        location="Bogotá",
        modality="Híbrido",
        salary="",
        url="https://example.com/fresh",
        description="",
        requirements="",
        published_at=None,
        found_at=datetime.now(UTC),
        raw_posted_text="hace 4 horas",
        source_id=1,
    )
    old_job = ScrapedJob(
        title="Backend",
        company="ACME",
        portal="indeed",
        location="Remoto",
        modality="Remoto",
        salary="",
        url="https://example.com/old",
        description="",
        requirements="",
        published_at=datetime.now(UTC) - timedelta(days=3),
        found_at=datetime.now(UTC),
        raw_posted_text="publicada hace 3 dias",
        source_id=1,
    )

    assert is_scraped_job_fresh(fresh_job) is True
    assert is_scraped_job_fresh(old_job) is False


def test_get_due_sources_handles_naive_aware_and_missing_last_checked_without_typeerror(tmp_path: Path):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        naive_source = add_source(
            session,
            portal="computrabajo",
            target_role="soporte_aplicaciones",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        aware_source = add_source(
            session,
            portal="linkedin",
            target_role="devops_trainee",
            search_url="https://linkedin.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        none_source = add_source(
            session,
            portal="torre",
            target_role="devops_trainee",
            search_url="https://torre.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        naive_source.last_checked_at = datetime(2026, 4, 29, 10, 0, 0)
        aware_source.last_checked_at = datetime(2026, 4, 29, 10, 0, 0, tzinfo=UTC)
        none_source.last_checked_at = None
        session.commit()

        due_sources = get_due_sources(session, now=datetime(2026, 4, 29, 10, 20, 0, tzinfo=UTC))

        due_ids = {source.id for source in due_sources}
        assert naive_source.id in due_ids
        assert aware_source.id in due_ids
        assert none_source.id in due_ids
