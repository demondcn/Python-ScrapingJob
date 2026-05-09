import csv
from datetime import UTC, datetime
import json
from argparse import Namespace
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.jobops_assistant.cli import (
    _handle_discarded_clear,
    _handle_discarded_export,
    _handle_discarded_list,
    _handle_discarded_reprocess,
    _handle_discarded_show,
)
from src.jobops_assistant.database import create_session_factory, create_sqlite_engine, init_db
from src.jobops_assistant.discarded_job_service import (
    DiscardedJobReview,
    list_discarded_jobs,
    upsert_discarded_job,
)
from src.jobops_assistant.freshness_monitor import run_fresh_monitor
from src.jobops_assistant.job_service import create_offer, list_offers
from src.jobops_assistant.models import JobSeenHash
from src.jobops_assistant.profile_service import upsert_profile
from src.jobops_assistant.scrapers.base_scraper import ScrapedJob
from src.jobops_assistant.search_sources import add_source, list_sources
from src.jobops_assistant.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "discarded.db",
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
    def __init__(self, jobs=None) -> None:
        self.jobs = jobs or []

    def scrape(self, source):
        return self.jobs


def _make_job(
    *,
    title: str,
    url: str,
    company: str = "ACME",
    portal: str = "magneto",
    description: str = "",
    requirements: str = "",
    raw_posted_text: str = "Hoy",
    source_id: int | None = 1,
) -> ScrapedJob:
    return ScrapedJob(
        title=title,
        company=company,
        portal=portal,
        location="Bogota",
        modality="Remoto",
        salary="",
        url=url,
        description=description,
        requirements=requirements,
        published_at=datetime.now(UTC),
        found_at=datetime.now(UTC),
        raw_posted_text=raw_posted_text,
        source_id=source_id,
    )


def _insert_discarded(session: Session, source, job: ScrapedJob, *, reasons: list[str], keywords: list[str], score: int = 55):
    return upsert_discarded_job(
        session,
        portal=source.portal,
        source_id=source.id,
        target_role=source.target_role,
        source_url=source.search_url,
        review=DiscardedJobReview(
            job=job,
            reasons=reasons,
            detected_keywords=keywords,
            preliminary_score=score,
        ),
    )


@pytest.mark.parametrize(
    ("portal", "target_role", "title", "description", "requirements"),
    [
        ("computrabajo", "infraestructura_junior", "Lead Data Intern", "Datos y reportes", "Intern"),
        ("elempleo", "devops_trainee", "Promotor junior libranza", "Ventas de libranza", "Junior"),
        ("magneto", "backend_junior", "Senior Backend Developer", "Java y Spring Boot", "5 anos de experiencia"),
        ("indeed", "backend_junior", "Senior Backend Developer", "Java y Spring Boot", "5 anos de experiencia"),
    ],
)
def test_discarded_offer_is_saved_for_any_portal_and_not_promoted_or_notified(
    tmp_path: Path,
    monkeypatch,
    portal: str,
    target_role: str,
    title: str,
    description: str,
    requirements: str,
):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)

    calls: list[list[int]] = []
    with Session(engine) as session:
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
            portal=portal,
            target_role=target_role,
            search_url=f"https://{portal}.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        discarded_job = _make_job(
            title=title,
            url=f"https://{portal}.example/jobs/1",
            portal=portal,
            description=description,
            requirements=requirements,
            source_id=source.id,
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper([discarded_job]),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: calls.append([offer.id for offer in offers]) or (True, "ok", offers),
        )

        logs = run_fresh_monitor(session, settings, force_all=True)

        discarded = list_discarded_jobs(session, limit=None)
        assert len(discarded) == 1
        assert discarded[0].title == title
        assert discarded[0].portal == portal
        assert discarded[0].target_role == target_role
        assert list_offers(session) == []
        assert session.scalars(select(JobSeenHash)).all() == []
        assert calls == []
        assert any("descartadas=1" in line for line in logs)


def test_discarded_same_url_updates_seen_count_and_last_seen(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)

    with Session(engine) as session:
        source = add_source(
            session,
            portal="magneto",
            target_role="backend_junior",
            search_url="https://magneto.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        discarded_job = _make_job(
            title="Senior Backend Developer",
            url="https://magneto.example/jobs/1",
            description="Java y Spring Boot",
            requirements="5 anos de experiencia",
            source_id=source.id,
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper([discarded_job]),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: (True, "ok", offers),
        )

        run_fresh_monitor(session, settings, force_all=True)
        first = list_discarded_jobs(session, limit=None)[0]
        first_seen = first.last_seen_at

        run_fresh_monitor(session, settings, force_all=True)
        records = list_discarded_jobs(session, limit=None)

        assert len(records) == 1
        assert records[0].seen_count == 2
        assert records[0].last_seen_at is not None
        assert first_seen is not None
        assert records[0].last_seen_at >= first_seen


def test_discarded_list_handler_prints_records(tmp_path: Path, capsys):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(engine) as session:
        source = add_source(
            session,
            portal="magneto",
            target_role="frontend_junior",
            search_url="https://magneto.example/front",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        record = _insert_discarded(
            session,
            source,
            _make_job(
                title="Administrador de Redes",
                url="https://magneto.example/jobs/2",
                description="Redes y soporte",
                requirements="Infraestructura",
                source_id=source.id,
            ),
            reasons=["parece backend o infraestructura por redes"],
            keywords=["redes", "infraestructura"],
        )

        code = _handle_discarded_list(
            Namespace(portal="magneto", target_role="frontend_junior", limit=20),
            session,
            settings,
            session_factory,
        )

        output = capsys.readouterr().out
        assert code == 0
        assert "Mostrando 1 descartadas." in output
        assert f"[{record.id}] Administrador de Redes | ACME | magneto | frontend_junior" in output
        assert "parece backend o infraestructura por redes" in output


def test_discarded_list_handler_filters_by_portal_and_explains_limit(tmp_path: Path, capsys):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(engine) as session:
        source_elempleo = add_source(
            session,
            portal="elempleo",
            target_role="devops_trainee",
            search_url="https://elempleo.example/devops",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        source_computrabajo = add_source(
            session,
            portal="computrabajo",
            target_role="infraestructura_junior",
            search_url="https://computrabajo.example/infra",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        elempleo_record = _insert_discarded(
            session,
            source_elempleo,
            _make_job(
                title="Promotor junior libranza",
                url="https://elempleo.example/jobs/1",
                portal="elempleo",
                description="Ventas",
                requirements="Junior",
                source_id=source_elempleo.id,
            ),
            reasons=["no coincide con devops_trainee"],
            keywords=["junior"],
        )
        computrabajo_record = _insert_discarded(
            session,
            source_computrabajo,
            _make_job(
                title="Lead Data Intern",
                url="https://computrabajo.example/jobs/1",
                portal="computrabajo",
                description="Datos",
                requirements="Intern",
                source_id=source_computrabajo.id,
            ),
            reasons=["contiene lead"],
            keywords=["lead"],
        )

        code_elempleo = _handle_discarded_list(
            Namespace(portal="elempleo", target_role=None, limit=20),
            session,
            settings,
            session_factory,
        )
        elempleo_output = capsys.readouterr().out

        code_computrabajo = _handle_discarded_list(
            Namespace(portal="computrabajo", target_role=None, limit=20),
            session,
            settings,
            session_factory,
        )
        computrabajo_output = capsys.readouterr().out

        code_limited = _handle_discarded_list(
            Namespace(portal=None, target_role=None, limit=1),
            session,
            settings,
            session_factory,
        )
        limited_output = capsys.readouterr().out

        assert code_elempleo == 0
        assert code_computrabajo == 0
        assert code_limited == 0
        assert f"[{elempleo_record.id}] Promotor junior libranza | ACME | elempleo | devops_trainee" in elempleo_output
        assert "computrabajo" not in elempleo_output
        assert f"[{computrabajo_record.id}] Lead Data Intern | ACME | computrabajo | infraestructura_junior" in computrabajo_output
        assert "elempleo" not in computrabajo_output
        assert "Mostrando 1 de 2 descartadas. Usa --limit para ampliar el listado." in limited_output


def test_discarded_show_handler_prints_detail(tmp_path: Path, capsys):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(engine) as session:
        source = add_source(
            session,
            portal="magneto",
            target_role="backend_junior",
            search_url="https://magneto.example/back",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        record = _insert_discarded(
            session,
            source,
            _make_job(
                title="Senior Backend Developer",
                url="https://magneto.example/jobs/3",
                description="Java y Spring Boot",
                requirements="5 anos",
                source_id=source.id,
            ),
            reasons=["contiene senior"],
            keywords=["senior", "spring boot"],
        )

        code = _handle_discarded_show(
            Namespace(id=record.id),
            session,
            settings,
            session_factory,
        )

        output = capsys.readouterr().out
        assert code == 0
        assert "title: Senior Backend Developer" in output
        assert "portal: magneto" in output
        assert "discard_reasons:" in output
        assert "contiene senior" in output
        assert "detected_keywords:" in output
        assert "spring boot" in output
        assert "raw_posted_text:" in output


def test_discarded_clear_removes_only_discarded(tmp_path: Path, capsys):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(engine) as session:
        source = add_source(
            session,
            portal="magneto",
            target_role="backend_junior",
            search_url="https://magneto.example/back",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        create_offer(
            session,
            title="Backend Junior",
            company="RealCo",
            portal="magneto",
            location="Bogota",
            modality="Remoto",
            salary="",
            url="https://magneto.example/jobs/real",
            description="Node.js y SQL",
            requirements="Junior",
        )
        _insert_discarded(
            session,
            source,
            _make_job(
                title="Senior Backend Developer",
                url="https://magneto.example/jobs/4",
                description="Java y Spring Boot",
                requirements="5 anos",
                source_id=source.id,
            ),
            reasons=["contiene senior"],
            keywords=["senior"],
        )

        code = _handle_discarded_clear(
            Namespace(portal=None, target_role=None, yes=True),
            session,
            settings,
            session_factory,
        )

        output = capsys.readouterr().out
        assert code == 0
        assert "Descartadas eliminadas: 1" in output
        assert list_discarded_jobs(session, limit=None) == []
        assert len(list_offers(session)) == 1
        assert len(list_sources(session)) == 1


def test_discarded_reprocess_can_convert_into_job_offer(tmp_path: Path, capsys):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(engine) as session:
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
            portal="magneto",
            target_role="backend_junior",
            search_url="https://magneto.example/back",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        record = _insert_discarded(
            session,
            source,
            _make_job(
                title="Backend Junior",
                url="https://magneto.example/jobs/5",
                description="Node.js, SQL y APIs REST",
                requirements="Junior",
                source_id=source.id,
            ),
            reasons=["descartada por matcher viejo"],
            keywords=["backend", "node.js"],
            score=82,
        )

        code = _handle_discarded_reprocess(
            Namespace(id=record.id, portal=None, target_role=None),
            session,
            settings,
            session_factory,
        )

        output = capsys.readouterr().out
        offers = list_offers(session)
        assert code == 0
        assert len(offers) == 1
        assert offers[0].title == "Backend Junior"
        assert list_discarded_jobs(session, limit=None) == []
        assert "aceptada -> job_offer" in output


def test_discarded_reprocess_by_portal_reclassifies_matching_jobs(tmp_path: Path, capsys):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(engine) as session:
        upsert_profile(
            session,
            full_name="Cris Perez",
            email="cris@example.com",
            phone="3000000000",
            city="Bogota",
            summary="Interes en infraestructura y frontend",
            skills="React,TypeScript,Soporte TI,Sistemas",
            projects="",
            education="Tecnologo",
            target_roles="frontend_junior,infraestructura_junior",
        )
        source_a = add_source(
            session,
            portal="computrabajo",
            target_role="infraestructura_junior",
            search_url="https://computrabajo.example/infra",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        source_b = add_source(
            session,
            portal="computrabajo",
            target_role="frontend_junior",
            search_url="https://computrabajo.example/front",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        _insert_discarded(
            session,
            source_a,
            _make_job(
                title="Practicante IT de sistemas",
                url="https://computrabajo.example/jobs/infra-1",
                portal="computrabajo",
                description="Soporte TI y mantenimiento de equipos",
                requirements="Practicante",
                source_id=source_a.id,
            ),
            reasons=["descartada por matcher viejo"],
            keywords=["it", "sistemas"],
            score=70,
        )
        _insert_discarded(
            session,
            source_b,
            _make_job(
                title="Desarrollador Front End Ecommerce",
                url="https://computrabajo.example/jobs/front-1",
                portal="computrabajo",
                description="React, CSS y JavaScript",
                requirements="Junior",
                source_id=source_b.id,
            ),
            reasons=["descartada por matcher viejo"],
            keywords=["frontend", "react"],
            score=78,
        )

        code = _handle_discarded_reprocess(
            Namespace(id=None, portal="computrabajo", target_role=None),
            session,
            settings,
            session_factory,
        )

        output = capsys.readouterr().out
        offers = list_offers(session)
        assert code == 0
        assert len(offers) == 2
        assert {offer.title for offer in offers} == {
            "Practicante IT de sistemas",
            "Desarrollador Front End Ecommerce",
        }
        assert list_discarded_jobs(session, limit=None) == []
        assert "Reprocesadas: 2 | aceptadas: 2 | descartadas: 0" in output


def test_discarded_export_generates_csv_and_json(tmp_path: Path, capsys):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(engine) as session:
        source = add_source(
            session,
            portal="magneto",
            target_role="backend_junior",
            search_url="https://magneto.example/back",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        _insert_discarded(
            session,
            source,
            _make_job(
                title="Senior Backend Developer",
                url="https://magneto.example/jobs/6",
                description="Java y Spring Boot",
                requirements="5 anos",
                source_id=source.id,
            ),
            reasons=["contiene senior"],
            keywords=["senior", "spring boot"],
        )

        csv_path = tmp_path / "discarded.csv"
        json_path = tmp_path / "discarded.json"
        csv_code = _handle_discarded_export(
            Namespace(file=str(csv_path), portal="magneto", target_role=None),
            session,
            settings,
            session_factory,
        )
        json_code = _handle_discarded_export(
            Namespace(file=str(json_path), portal="magneto", target_role=None),
            session,
            settings,
            session_factory,
        )

        assert csv_code == 0
        assert json_code == 0
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1
        assert rows[0]["portal"] == "magneto"
        assert "contiene senior" in rows[0]["discard_reasons"]

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["title"] == "Senior Backend Developer"
        assert data[0]["seen_count"] == 1

        output = capsys.readouterr().out
        assert "Descartadas exportadas: 1" in output


def test_monitor_can_accept_job_after_it_was_previously_discarded(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)

    with Session(engine) as session:
        upsert_profile(
            session,
            full_name="Cris Perez",
            email="cris@example.com",
            phone="3000000000",
            city="Bogota",
            summary="Interes en frontend",
            skills="React,TypeScript,CSS",
            projects="",
            education="Tecnologo",
            target_roles="frontend_junior",
        )
        source = add_source(
            session,
            portal="magneto",
            target_role="frontend_junior",
            search_url="https://magneto.example/front",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        _insert_discarded(
            session,
            source,
            _make_job(
                title="Administrador de Redes",
                url="https://magneto.example/jobs/7",
                description="Redes y cableado",
                requirements="Infraestructura",
                source_id=source.id,
            ),
            reasons=["parece backend o infraestructura por redes"],
            keywords=["redes", "infraestructura"],
        )

        accepted_job = _make_job(
            title="Frontend Junior",
            url="https://magneto.example/jobs/7",
            description="React, CSS, JavaScript y componentes UI",
            requirements="Junior",
            source_id=source.id,
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.get_scraper",
            lambda portal, settings: _FakeScraper([accepted_job]),
        )
        monkeypatch.setattr(
            "src.jobops_assistant.freshness_monitor.send_job_alert_digest",
            lambda offers, settings, title=None: (True, "ok", offers),
        )

        run_fresh_monitor(session, settings, force_all=True)

        assert len(list_offers(session)) == 1
        assert list_offers(session)[0].title == "Frontend Junior"
        assert list_discarded_jobs(session, limit=None) == []
