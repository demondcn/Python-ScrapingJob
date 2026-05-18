from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from src.jobops_assistant.application_types import EXTERNAL_APPLY, LINKEDIN_EASY_APPLY, UNKNOWN_APPLICATION_TYPE
from src.jobops_assistant.database import create_session_factory, create_sqlite_engine, init_db
from src.jobops_assistant.discarded_job_service import list_discarded_jobs, parse_text_list
from src.jobops_assistant.freshness_monitor import is_scraped_job_fresh, retry_pending_alerts, run_fresh_monitor
from src.jobops_assistant.job_service import list_offers, list_pending_alert_offers
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
        telegram_digest_max_jobs=10,
        telegram_max_message_chars=3500,
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


def _backend_job(
    *,
    portal: str,
    url: str,
    source_id: int,
    title: str = "Backend Junior",
) -> ScrapedJob:
    return ScrapedJob(
        title=title,
        company="API Labs",
        portal=portal,
        location="Bogota",
        modality="Remoto",
        salary="",
        url=url,
        description="Backend junior con Node.js, APIs REST, SQL y Git.",
        requirements="Junior",
        published_at=datetime.now(UTC),
        found_at=datetime.now(UTC),
        raw_posted_text="Publicada hoy",
        source_id=source_id,
    )


def test_monitor_saves_new_offers_dedupes_and_notifies_once(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)
    calls: list[list[int]] = []

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
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, "Digest enviado por Telegram con 1 ofertas.", offers),
        )

        first_logs = run_fresh_monitor(session, settings, force_all=True)
        second_logs = run_fresh_monitor(session, settings, force_all=True)

        offers = list_offers(session)
        assert len(offers) == 1
        assert offers[0].title == "Soporte de Aplicaciones Junior"
        assert calls == [[offers[0].id]]
        assert offers[0].telegram_notified is True
        assert any("nuevas=1" in line for line in first_logs)
        assert any("duplicados=1" in line for line in second_logs)
        assert any("digest enviado con 1 ofertas" in line for line in first_logs)


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
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: (True, "Digest enviado por Telegram con 1 ofertas.", offers),
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
        assert failed_source.failure_count == 1
        assert ok_source is not None and ok_source.last_error == ""


def test_blocked_source_is_paused_after_three_failures_and_skipped(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)

    with Session(engine) as session:
        source = add_source(
            session,
            portal="elempleo",
            target_role="backend_junior",
            search_url="https://elempleo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper(error=SourceBlockedError("La fuente solicito captcha. Se omite esta fuente.")),
        )

        run_fresh_monitor(session, settings, force_all=True)
        run_fresh_monitor(session, settings, force_all=True)
        third_logs = run_fresh_monitor(session, settings, force_all=True)
        paused_source = get_source_by_id(session, source.id)

        assert paused_source is not None
        assert paused_source.failure_count == 3
        assert paused_source.paused_until is not None
        assert any("pausada hasta" in line.lower() for line in third_logs)

        skipped_logs = run_fresh_monitor(session, settings, force_all=False)
        assert any("pausada hasta" in line.lower() for line in skipped_logs)


def test_successful_source_resets_failure_state(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)

    with Session(engine) as session:
        source = add_source(
            session,
            portal="computrabajo",
            target_role="backend_junior",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        source.failure_count = 2
        source.last_error = "captcha"
        source.last_failed_at = datetime.now(UTC) - timedelta(hours=3)
        session.commit()

        job = ScrapedJob(
            title="Backend Junior",
            company="ABC Tecnologia",
            portal="computrabajo",
            location="Bogota",
            modality="Remoto",
            salary="",
            url="https://computrabajo.example/jobs/401",
            description="Node.js, APIs REST, SQL y Git.",
            requirements="Junior",
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
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: (True, "Digest enviado por Telegram con 1 ofertas.", offers),
        )

        run_fresh_monitor(session, settings, force_all=True)
        updated = get_source_by_id(session, source.id)

        assert updated is not None
        assert updated.failure_count == 0
        assert updated.last_error == ""
        assert updated.paused_until is None
        assert updated.last_failed_at is None


def test_monitor_calls_telegram_only_above_threshold(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)
    calls: list[list[int]] = []

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
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, "Digest enviado por Telegram con 1 ofertas.", offers),
        )

        logs = run_fresh_monitor(session, settings, force_all=True)

        offers = list_offers(session)
        assert len(offers) == 1
        assert len(calls) == 1
        assert len(calls[0]) == 1
        assert any("descartadas=1" in line for line in logs)


def test_monitor_accepts_linkedin_easy_apply_when_only_easy_apply_is_enabled(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    settings.linkedin_only_easy_apply = True
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    calls: list[list[int]] = []

    with Session(engine) as session:
        source = add_source(
            session,
            portal="linkedin_selenium",
            target_role="backend_junior",
            search_url="https://linkedin.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        job = ScrapedJob(
            title="Backend Junior",
            company="Acme API",
            portal="linkedin_selenium",
            location="Colombia",
            modality="Remoto",
            salary="",
            url="https://www.linkedin.com/jobs/view/7001",
            description="Backend junior con Node.js, APIs, SQL y Git.",
            requirements="Junior",
            published_at=datetime.now(UTC),
            found_at=datetime.now(UTC),
            raw_posted_text="Hace 1 hora",
            source_id=source.id,
            application_type=LINKEDIN_EASY_APPLY,
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper([job]),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, "Digest enviado por Telegram con 1 ofertas.", offers),
        )

        logs = run_fresh_monitor(session, settings, force_all=True)

        offers = list_offers(session)
        assert len(offers) == 1
        assert offers[0].portal == "linkedin_selenium"
        assert offers[0].application_type == LINKEDIN_EASY_APPLY
        assert list_discarded_jobs(session, limit=None) == []
        assert calls == [[offers[0].id]]
        assert any("nuevas=1" in line for line in logs)


def test_monitor_discards_linkedin_external_apply_when_only_easy_apply_is_enabled(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    settings.linkedin_only_easy_apply = True
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    calls: list[list[int]] = []

    with Session(engine) as session:
        source = add_source(
            session,
            portal="linkedin_selenium",
            target_role="backend_junior",
            search_url="https://linkedin.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        job = ScrapedJob(
            title="Backend Junior",
            company="Acme API",
            portal="linkedin_selenium",
            location="Colombia",
            modality="Remoto",
            salary="",
            url="https://www.linkedin.com/jobs/view/7002",
            description="Backend junior con Node.js, APIs, SQL y Git.",
            requirements="Junior",
            published_at=datetime.now(UTC),
            found_at=datetime.now(UTC),
            raw_posted_text="Hace 1 hora",
            source_id=source.id,
            application_type=EXTERNAL_APPLY,
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper([job]),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, "ok", offers),
        )

        logs = run_fresh_monitor(session, settings, force_all=True)

        discarded = list_discarded_jobs(session, limit=None)
        assert list_offers(session) == []
        assert len(discarded) == 1
        assert parse_text_list(discarded[0].discard_reasons) == ["no es solicitud sencilla de LinkedIn"]
        assert discarded[0].application_type == EXTERNAL_APPLY
        assert calls == []
        assert any("descartadas=1" in line for line in logs)


def test_monitor_discards_linkedin_unknown_apply_when_only_easy_apply_is_enabled(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    settings.linkedin_only_easy_apply = True
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    calls: list[list[int]] = []

    with Session(engine) as session:
        source = add_source(
            session,
            portal="linkedin_selenium",
            target_role="backend_junior",
            search_url="https://linkedin.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        job = ScrapedJob(
            title="Backend Junior",
            company="Acme API",
            portal="linkedin_selenium",
            location="Colombia",
            modality="Remoto",
            salary="",
            url="https://www.linkedin.com/jobs/view/7003",
            description="Backend junior con Node.js, APIs, SQL y Git.",
            requirements="Junior",
            published_at=datetime.now(UTC),
            found_at=datetime.now(UTC),
            raw_posted_text="Hace 1 hora",
            source_id=source.id,
            application_type=UNKNOWN_APPLICATION_TYPE,
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper([job]),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, "ok", offers),
        )

        run_fresh_monitor(session, settings, force_all=True)

        discarded = list_discarded_jobs(session, limit=None)
        assert list_offers(session) == []
        assert len(discarded) == 1
        assert parse_text_list(discarded[0].discard_reasons) == ["no es solicitud sencilla de LinkedIn"]
        assert discarded[0].application_type == UNKNOWN_APPLICATION_TYPE
        assert calls == []


def test_monitor_only_easy_apply_rule_does_not_affect_other_portals(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    settings.linkedin_only_easy_apply = True
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    calls: list[list[int]] = []

    with Session(engine) as session:
        jobs: list[ScrapedJob] = []
        for index, portal in enumerate(("computrabajo", "elempleo", "magneto", "indeed"), start=1):
            source = add_source(
                session,
                portal=portal,
                target_role="backend_junior",
                search_url=f"https://{portal}.example/jobs",
                interval_minutes=15,
                min_interval_minutes=settings.min_monitor_interval_minutes,
            )
            jobs.append(
                ScrapedJob(
                    title=f"Backend Junior {portal}",
                    company="Acme API",
                    portal=portal,
                    location="Colombia",
                    modality="Remoto",
                    salary="",
                    url=f"https://{portal}.example/jobs/{index}",
                    description="Backend junior con Node.js, APIs, SQL y Git.",
                    requirements="Junior",
                    published_at=datetime.now(UTC),
                    found_at=datetime.now(UTC),
                    raw_posted_text="Hoy",
                    source_id=source.id,
                    application_type=UNKNOWN_APPLICATION_TYPE,
                )
            )

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper([job for job in jobs if job.portal == portal]),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, f"Digest enviado por Telegram con {len(offers)} ofertas.", offers),
        )

        logs = run_fresh_monitor(session, settings, force_all=True)

        offers = list_offers(session)
        assert len(offers) == 4
        assert {offer.portal for offer in offers} == {"computrabajo", "elempleo", "magneto", "indeed"}
        assert list_discarded_jobs(session, limit=None) == []
        assert len(calls) == 1
        assert len(calls[0]) == 4
        assert all("descartadas=0" in line for line in logs if "encontradas=" in line)


def test_monitor_groups_multiple_notifiable_offers_in_single_digest_call(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)
    calls: list[list[int]] = []

    with Session(session_factory.kw["bind"]) as session:
        add_source(
            session,
            portal="computrabajo",
            target_role="fullstack_junior",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        jobs = [
            ScrapedJob(
                title="Full Stack Junior",
                company="ABC Tecnologia",
                portal="computrabajo",
                location="Bogota",
                modality="Remoto",
                salary="",
                url="https://computrabajo.example/jobs/301",
                description="React, Node.js, SQL, panel administrativo y APIs.",
                requirements="Junior",
                published_at=datetime.now(UTC),
                found_at=datetime.now(UTC),
                raw_posted_text="Publicada hoy",
                source_id=1,
            ),
            ScrapedJob(
                title="Desarrollador Full Stack",
                company="UI Labs",
                portal="computrabajo",
                location="Bogota",
                modality="Hibrido",
                salary="",
                url="https://computrabajo.example/jobs/302",
                description="Next.js, Node.js, PostgreSQL y e-commerce.",
                requirements="1 ano",
                published_at=datetime.now(UTC),
                found_at=datetime.now(UTC),
                raw_posted_text="Publicada hoy",
                source_id=1,
            ),
            ScrapedJob(
                title="Full Stack Junior",
                company="App Labs",
                portal="computrabajo",
                location="Bogota",
                modality="Hibrido",
                salary="",
                url="https://computrabajo.example/jobs/303",
                description="React, Node.js, PostgreSQL y despliegue.",
                requirements="Junior",
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
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, "Digest enviado por Telegram con 3 ofertas.", offers),
        )

        logs = run_fresh_monitor(session, settings, force_all=True)

        assert len(calls) == 1
        assert len(calls[0]) == 3
        assert any("queued_alerts=3" in line for line in logs)
        assert any("digest enviado con 3 ofertas" in line for line in logs)


def test_monitor_sends_telegram_immediately_after_each_source(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    settings.notify_after_each_source = True
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    calls: list[list[int | None]] = []

    with Session(engine) as session:
        source_1 = add_source(
            session,
            portal="computrabajo",
            target_role="backend_junior",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        source_2 = add_source(
            session,
            portal="elempleo",
            target_role="backend_junior",
            search_url="https://elempleo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        source_3 = add_source(
            session,
            portal="magneto",
            target_role="backend_junior",
            search_url="https://magneto.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        jobs_by_portal = {
            "computrabajo": [
                _backend_job(portal="computrabajo", url="https://computrabajo.example/jobs/1", source_id=source_1.id, title="Backend Junior A"),
                _backend_job(portal="computrabajo", url="https://computrabajo.example/jobs/2", source_id=source_1.id, title="Backend Junior B"),
            ],
            "elempleo": [],
            "magneto": [
                _backend_job(portal="magneto", url="https://magneto.example/jobs/3", source_id=source_3.id, title="Backend Junior C"),
            ],
        }

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper(jobs_by_portal[portal]),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.source_id for offer in offers]) or (True, f"Digest enviado por Telegram con {len(offers)} ofertas.", offers),
        )

        logs = run_fresh_monitor(session, settings, force_all=True)

        offers = list_offers(session)
        assert len(offers) == 3
        assert all(offer.telegram_notified for offer in offers)
        assert calls == [[source_1.id, source_1.id], [source_3.id]]
        assert any(f"[telegram] envío inmediato con 2 ofertas desde fuente {source_1.id}" in line for line in logs)
        assert any(f"[telegram] sin ofertas nuevas para fuente {source_2.id}" in line for line in logs)
        assert any(f"[telegram] envío inmediato con 1 ofertas desde fuente {source_3.id}" in line for line in logs)
        assert any("[telegram] no hay pendientes al final del ciclo" in line for line in logs)


def test_monitor_immediate_send_does_not_duplicate_at_final_digest(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    settings.notify_after_each_source = True
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    calls: list[list[int | None]] = []

    with Session(engine) as session:
        source = add_source(
            session,
            portal="computrabajo",
            target_role="backend_junior",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        jobs = [
            _backend_job(portal="computrabajo", url="https://computrabajo.example/jobs/401", source_id=source.id),
        ]

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper(jobs),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, "Digest enviado por Telegram con 1 ofertas.", offers),
        )

        logs = run_fresh_monitor(session, settings, force_all=True)

        assert len(calls) == 1
        assert list_pending_alert_offers(session, threshold=settings.match_threshold) == []
        assert any("[telegram] no hay pendientes al final del ciclo" in line for line in logs)


def test_monitor_immediate_send_failure_leaves_offer_pending_for_retry(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    settings.notify_after_each_source = True
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    calls: list[list[int | None]] = []

    with Session(engine) as session:
        source = add_source(
            session,
            portal="computrabajo",
            target_role="backend_junior",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        jobs = [
            _backend_job(portal="computrabajo", url="https://computrabajo.example/jobs/402", source_id=source.id),
        ]

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper(jobs),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (False, "Error enviando digest por Telegram: fail", []),
        )

        logs = run_fresh_monitor(session, settings, force_all=True)

        offers = list_offers(session)
        pending = list_pending_alert_offers(session, threshold=settings.match_threshold)
        assert len(calls) == 1
        assert len(offers) == 1
        assert offers[0].telegram_notified is False
        assert [offer.id for offer in pending] == [offers[0].id]
        assert any(f"[telegram] fallo envio inmediato para fuente {source.id}" in line for line in logs)


def test_monitor_digest_at_end_when_notify_after_each_source_is_disabled(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    settings.notify_after_each_source = False
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    calls: list[list[int | None]] = []

    with Session(engine) as session:
        source_1 = add_source(
            session,
            portal="computrabajo",
            target_role="backend_junior",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        source_2 = add_source(
            session,
            portal="magneto",
            target_role="backend_junior",
            search_url="https://magneto.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        jobs_by_portal = {
            "computrabajo": [
                _backend_job(portal="computrabajo", url="https://computrabajo.example/jobs/501", source_id=source_1.id),
            ],
            "magneto": [
                _backend_job(portal="magneto", url="https://magneto.example/jobs/502", source_id=source_2.id),
            ],
        }

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper(jobs_by_portal[portal]),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.source_id for offer in offers]) or (True, f"Digest enviado por Telegram con {len(offers)} ofertas.", offers),
        )

        logs = run_fresh_monitor(session, settings, force_all=True)

        assert len(calls) == 1
        assert sorted(calls[0]) == sorted([source_1.id, source_2.id])
        assert not any("envío inmediato" in line for line in logs)
        assert any("digest enviado con 2 ofertas" in line for line in logs)


def test_monitor_discards_noise_for_backend_target(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)

    with Session(engine) as session:
        add_source(
            session,
            portal="elempleo",
            target_role="backend_junior",
            search_url="https://elempleo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        jobs = [
            ScrapedJob(
                title="Backend Junior",
                company="ABC",
                portal="elempleo",
                location="Bogota",
                modality="Remoto",
                salary="",
                url="https://elempleo.example/jobs/1",
                description="Node.js, SQL y APIs REST.",
                requirements="Junior",
                published_at=datetime.now(UTC),
                found_at=datetime.now(UTC),
                raw_posted_text="Hoy",
                source_id=1,
            ),
            ScrapedJob(
                title="Senior Backend Developer",
                company="XYZ",
                portal="elempleo",
                location="Bogota",
                modality="Remoto",
                salary="",
                url="https://elempleo.example/jobs/2",
                description="Java, Spring Boot y microservicios.",
                requirements="5 anos de experiencia",
                published_at=datetime.now(UTC),
                found_at=datetime.now(UTC),
                raw_posted_text="Hoy",
                source_id=1,
            ),
            ScrapedJob(
                title="Disenador UX",
                company="DesignCo",
                portal="elempleo",
                location="Bogota",
                modality="Hibrido",
                salary="",
                url="https://elempleo.example/jobs/3",
                description="UI, Figma y experiencia de usuario.",
                requirements="Junior",
                published_at=datetime.now(UTC),
                found_at=datetime.now(UTC),
                raw_posted_text="Hoy",
                source_id=1,
            ),
        ]
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper(jobs),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: (True, "Digest enviado por Telegram con 1 ofertas.", offers),
        )

        logs = run_fresh_monitor(session, settings, force_all=True)
        offers = list_offers(session)

        assert len(offers) == 1
        assert offers[0].title == "Backend Junior"
        assert any("descartadas=2" in line for line in logs)


def test_monitor_keeps_offers_pending_if_digest_fails(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        add_source(
            session,
            portal="computrabajo",
            target_role="backend_junior",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        job = ScrapedJob(
            title="Backend Junior",
            company="ABC Tecnologia",
            portal="computrabajo",
            location="Bogota",
            modality="Remoto",
            salary="",
            url="https://computrabajo.example/jobs/304",
            description="Node.js, APIs REST, SQL y Git.",
            requirements="Junior",
            published_at=datetime.now(UTC),
            found_at=datetime.now(UTC),
            raw_posted_text="Publicada hoy",
            source_id=1,
        )

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper([job]),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: (False, "Error enviando digest por Telegram: fail", []),
        )

        run_fresh_monitor(session, settings, force_all=True)

        offer = list_offers(session)[0]
        assert offer.telegram_notified is False


def test_retry_pending_alerts_marks_notified_if_one_telegram_chat_succeeds(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    settings.telegram_bot_token = "token"
    settings.telegram_chat_ids = ["bad-chat", "good-chat"]
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    attempts: list[str] = []

    with Session(engine) as session:
        offer = JobOffer(
            title="Backend Junior",
            company="API Labs",
            portal="computrabajo",
            location="Bogota",
            modality="Remoto",
            salary="",
            url="https://example.com/retry-partial-chat",
            description="Node.js y SQL",
            requirements="Junior",
            compatibility_score=90,
            telegram_notified=False,
        )
        session.add(offer)
        session.commit()
        session.refresh(offer)

        def _post(settings, message, chat_id):
            attempts.append(chat_id)
            if chat_id == "bad-chat":
                raise RuntimeError("fail")

        monkeypatch.setattr("src.jobops_assistant.telegram_notifier._post_telegram_message_to_chat", _post)

        retry_pending_alerts(session, settings)
        session.refresh(offer)

        assert attempts == ["bad-chat", "good-chat"]
        assert offer.telegram_notified is True
        assert list_pending_alert_offers(session, threshold=settings.match_threshold) == []


def test_retry_pending_alerts_keeps_pending_if_all_telegram_chats_fail(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    settings.telegram_bot_token = "token"
    settings.telegram_chat_ids = ["bad-chat", "other-bad-chat"]
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    attempts: list[str] = []

    with Session(engine) as session:
        offer = JobOffer(
            title="Backend Junior",
            company="API Labs",
            portal="computrabajo",
            location="Bogota",
            modality="Remoto",
            salary="",
            url="https://example.com/retry-all-chats-fail",
            description="Node.js y SQL",
            requirements="Junior",
            compatibility_score=90,
            telegram_notified=False,
        )
        session.add(offer)
        session.commit()
        session.refresh(offer)

        def _post(settings, message, chat_id):
            attempts.append(chat_id)
            raise RuntimeError("fail")

        monkeypatch.setattr("src.jobops_assistant.telegram_notifier._post_telegram_message_to_chat", _post)

        logs = retry_pending_alerts(session, settings)
        session.refresh(offer)
        pending = list_pending_alert_offers(session, threshold=settings.match_threshold)

        assert attempts == ["bad-chat", "other-bad-chat"]
        assert offer.telegram_notified is False
        assert [pending_offer.id for pending_offer in pending] == [offer.id]
        assert any("fallo en todos los chats, se deja pendiente" in line for line in logs)



def test_monitor_retries_duplicate_offer_if_not_notified(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)
    calls: list[list[int]] = []

    with Session(session_factory.kw["bind"]) as session:
        upsert_profile(
            session,
            full_name="Cris Perez",
            email="cris@example.com",
            phone="3000000000",
            city="Bogota",
            summary="Interes en backend",
            skills="Node.js,SQL,Git",
            projects="",
            education="Tecnologo",
            target_roles="backend_junior",
        )
        source = add_source(
            session,
            portal="computrabajo",
            target_role="backend_junior",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        job = ScrapedJob(
            title="Backend Junior",
            company="ABC Tecnologia",
            portal="computrabajo",
            location="Bogota",
            modality="Remoto",
            salary="",
            url="https://computrabajo.example/jobs/200",
            description="Backend junior con Node.js, SQL, Git y APIs REST.",
            requirements="Junior",
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
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: (False, "Credenciales de Telegram incompletas; no se envio notificacion.", []),
        )
        run_fresh_monitor(session, settings, force_all=True)

        created_offer = list_offers(session)[0]
        assert created_offer.telegram_notified is False

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, "Digest enviado por Telegram con 1 ofertas.", offers),
        )
        logs = run_fresh_monitor(session, settings, force_all=True)

        updated_offer = list_offers(session)[0]
        assert updated_offer.telegram_notified is True
        assert calls == [[updated_offer.id]]
        assert any("pending_alerts=1" in line for line in logs)
        assert any("queued_alerts=1" in line for line in logs)


def test_monitor_does_not_resend_duplicate_offer_if_already_notified(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)
    calls: list[list[int]] = []

    with Session(session_factory.kw["bind"]) as session:
        upsert_profile(
            session,
            full_name="Cris Perez",
            email="cris@example.com",
            phone="3000000000",
            city="Bogota",
            summary="Interes en soporte",
            skills="SQL,Git",
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
            location="Bogota",
            modality="Hibrido",
            salary="",
            url="https://computrabajo.example/jobs/201",
            description="Soporte, SQL, tickets y documentacion.",
            requirements="Junior",
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
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, "Digest enviado por Telegram con 1 ofertas.", offers),
        )

        run_fresh_monitor(session, settings, force_all=True)
        run_fresh_monitor(session, settings, force_all=True)

        assert len(calls) == 1


def test_retry_pending_alerts_sends_saved_offers(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)
    calls: list[list[int]] = []

    with Session(session_factory.kw["bind"]) as session:
        offer = JobOffer(
            title="Frontend Junior",
            company="UI Labs",
            portal="computrabajo",
            location="Bogota",
            modality="Remoto",
            salary="",
            url="https://example.com/frontend",
            description="React, Next.js, TypeScript y Vercel.",
            requirements="Junior",
            compatibility_score=82,
            telegram_notified=False,
        )
        session.add(offer)
        session.commit()
        session.refresh(offer)

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, "Digest enviado por Telegram con 1 ofertas.", offers),
        )

        logs = retry_pending_alerts(session, settings)
        updated = list_offers(session)[0]

        assert updated.telegram_notified is True
        assert calls == [[updated.id]]
        assert any("digest enviado con 1 ofertas" in line.lower() for line in logs)


def test_retry_pending_alerts_sends_grouped_digest_once(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)
    calls: list[list[int]] = []

    with Session(session_factory.kw["bind"]) as session:
        offer_a = JobOffer(
            title="Backend Junior",
            company="API Labs",
            portal="computrabajo",
            location="Bogota",
            modality="Remoto",
            salary="",
            url="https://example.com/retry-a",
            description="Node.js",
            requirements="Junior",
            compatibility_score=78,
            telegram_notified=False,
        )
        offer_b = JobOffer(
            title="Frontend Junior",
            company="UI Labs",
            portal="computrabajo",
            location="Bogota",
            modality="Remoto",
            salary="",
            url="https://example.com/retry-b",
            description="React",
            requirements="Junior",
            compatibility_score=79,
            telegram_notified=False,
        )
        session.add_all([offer_a, offer_b])
        session.commit()
        session.refresh(offer_a)
        session.refresh(offer_b)

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, "Digest enviado por Telegram con 2 ofertas.", offers),
        )

        logs = retry_pending_alerts(session, settings)

        assert calls == [[offer_b.id, offer_a.id]] or calls == [[offer_a.id, offer_b.id]]
        assert any("digest enviado con 2 ofertas" in line.lower() for line in logs)


def test_retry_pending_alerts_retries_only_not_delivered_after_partial_failure(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)
    calls: list[list[int]] = []

    with Session(session_factory.kw["bind"]) as session:
        offers = [
            JobOffer(
                title=f"Backend Junior {index}",
                company="API Labs",
                portal="computrabajo",
                location="Bogota",
                modality="Remoto",
                salary="",
                url=f"https://example.com/retry-partial-{index}",
                description="Node.js y SQL",
                requirements="Junior",
                compatibility_score=90 - index,
                telegram_notified=False,
            )
            for index in range(4)
        ]
        session.add_all(offers)
        session.commit()
        for offer in offers:
            session.refresh(offer)

        def _partial_send(queued_offers, settings, title=None):
            calls.append([offer.id for offer in queued_offers])
            return False, "Error enviando digest por Telegram: fail", queued_offers[:2]

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            _partial_send,
        )

        first_logs = retry_pending_alerts(session, settings)
        after_partial = list_pending_alert_offers(session, threshold=settings.match_threshold)

        assert any("Error enviando digest por Telegram" in line for line in first_logs)
        assert len(after_partial) == 2
        assert {offer.id for offer in after_partial} == {offers[2].id, offers[3].id}

        def _successful_retry(queued_offers, settings, title=None):
            calls.append([offer.id for offer in queued_offers])
            return True, f"digest enviado con {len(queued_offers)} ofertas", queued_offers

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            _successful_retry,
        )

        second_logs = retry_pending_alerts(session, settings)

        assert calls[1] == [offers[2].id, offers[3].id]
        assert list_pending_alert_offers(session, threshold=settings.match_threshold) == []
        assert any("digest enviado con 2 ofertas" in line.lower() for line in second_logs)


def test_monitor_fresh_notify_pending_retries_existing_pending(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)
    calls: list[list[int]] = []

    with Session(session_factory.kw["bind"]) as session:
        source = add_source(
            session,
            portal="computrabajo",
            target_role="frontend_junior",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        pending_offer = JobOffer(
            title="Frontend Junior",
            company="UI Labs",
            portal="computrabajo",
            location="Bogota",
            modality="Remoto",
            salary="",
            url="https://example.com/frontend-pending",
            description="React, Next.js, TypeScript y Vercel.",
            requirements="Junior",
            compatibility_score=83,
            telegram_notified=False,
            source_id=source.id,
        )
        session.add(pending_offer)
        session.commit()

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper([]),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, "Digest enviado por Telegram con 1 ofertas.", offers),
        )

        logs = run_fresh_monitor(session, settings, force_all=True, notify_pending=True)

        assert calls == [[pending_offer.id]]
        assert any("pending_alerts=1" in line for line in logs)
        assert list_pending_alert_offers(session, threshold=settings.match_threshold) == []


def test_retry_pending_alerts_does_not_fail_with_invalid_timezone(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    settings.timezone_name = "Invalid/Timezone"
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        offer = JobOffer(
            title="Backend Junior",
            company="API Labs",
            portal="computrabajo",
            location="Bogota",
            modality="Remoto",
            salary="",
            url="https://example.com/backend-invalid-tz",
            description="Node.js y SQL",
            requirements="Junior",
            compatibility_score=78,
            telegram_notified=False,
        )
        session.add(offer)
        session.commit()

        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: (True, "Digest enviado por Telegram con 1 ofertas.", offers),
        )

        logs = retry_pending_alerts(session, settings)

        assert any("digest enviado con 1 ofertas" in line.lower() for line in logs)


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
