from argparse import Namespace
from pathlib import Path

from sqlalchemy.orm import Session

from src.jobops_assistant.cli import _handle_offer_clear
from src.jobops_assistant.database import create_session_factory, create_sqlite_engine, init_db
from src.jobops_assistant.job_service import clear_offers, create_offer, list_offers
from src.jobops_assistant.models import GeneratedDocument, JobOffer, JobSeenHash, JobSearchSource, Notification
from src.jobops_assistant.search_sources import add_source
from src.jobops_assistant.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "offers.db",
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


def test_clear_offers_preserves_sources_and_hashes(tmp_path: Path):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        add_source(
            session,
            portal="computrabajo",
            target_role="soporte_aplicaciones",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        offer = create_offer(
            session,
            title="Soporte de Aplicaciones Junior",
            company="ABC",
            portal="computrabajo",
            location="Bogota",
            modality="Hibrido",
            salary="",
            url="https://example.com/1",
            description="Soporte y SQL",
            requirements="Junior",
            normalized_url="https://example.com/1",
            url_hash="hash-1",
        )
        session.add(JobSeenHash(url_hash="hash-1", normalized_url="https://example.com/1", portal="computrabajo"))
        session.add(Notification(job_offer_id=offer.id, channel="telegram", status="sent", message="ok"))
        session.add(GeneratedDocument(job_offer_id=offer.id, doc_type="cv", file_path="generated/cvs/test.docx"))
        session.commit()

        result = clear_offers(session)

        assert result["offers_deleted"] == 1
        assert result["hashes_deleted"] == 1
        assert session.query(JobOffer).count() == 0
        assert session.query(JobSeenHash).count() == 0
        assert session.query(Notification).count() == 0
        assert session.query(GeneratedDocument).count() == 0
        assert session.query(JobSearchSource).count() == 1


def test_clear_offers_by_portal_only_removes_matching_portal(tmp_path: Path):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        create_offer(
            session,
            title="Soporte",
            company="ABC",
            portal="computrabajo",
            location="Bogota",
            modality="Hibrido",
            salary="",
            url="https://example.com/1",
            description="SQL",
            requirements="Junior",
            normalized_url="https://example.com/1",
            url_hash="hash-1",
        )
        create_offer(
            session,
            title="Frontend",
            company="XYZ",
            portal="linkedin",
            location="Remote",
            modality="Remoto",
            salary="",
            url="https://example.com/2",
            description="React",
            requirements="Junior",
            normalized_url="https://example.com/2",
            url_hash="hash-2",
        )
        session.add(JobSeenHash(url_hash="hash-1", normalized_url="https://example.com/1", portal="computrabajo"))
        session.add(JobSeenHash(url_hash="hash-2", normalized_url="https://example.com/2", portal="linkedin"))
        session.commit()

        result = clear_offers(session, portal="computrabajo")

        offers = list_offers(session)
        assert result["offers_deleted"] == 1
        assert result["hashes_deleted"] == 1
        assert len(offers) == 1
        assert offers[0].portal == "linkedin"
        assert session.query(JobSeenHash).count() == 1


def test_offer_clear_requires_confirmation_without_yes(tmp_path: Path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)
    args = Namespace(portal=None, yes=False)

    with Session(session_factory.kw["bind"]) as session:
        create_offer(
            session,
            title="Soporte",
            company="ABC",
            portal="computrabajo",
            location="Bogota",
            modality="Hibrido",
            salary="",
            url="https://example.com/1",
            description="SQL",
            requirements="Junior",
        )
        monkeypatch.setattr("builtins.input", lambda _: "no")

        code = _handle_offer_clear(args, session, settings, session_factory)

        assert code == 1
        assert len(list_offers(session)) == 1
        assert "Operacion cancelada." in capsys.readouterr().out
