from datetime import UTC, datetime
from argparse import Namespace
from pathlib import Path

from sqlalchemy.orm import Session

from src.jobops_assistant.cli import (
    _handle_sources_test,
    _handle_sources_disable_blocked,
    _handle_sources_unpause,
    _handle_sources_update_interval,
)
from src.jobops_assistant.database import create_session_factory, create_sqlite_engine, init_db
from src.jobops_assistant.discarded_job_service import DiscardedJobReview
from src.jobops_assistant.search_sources import (
    SourceTestResult,
    add_source,
    disable_blocked_sources,
    get_source_by_id,
    get_due_sources,
    record_source_failure,
    list_sources,
    ensure_utc,
    unpause_source_by_id,
    unpause_sources_by_portal,
    update_portal_source_intervals,
    update_source_interval,
)
from src.jobops_assistant.settings import Settings
from src.jobops_assistant.scrapers.base_scraper import ResponseDebugSnapshot, ScrapedJob


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "sources.db",
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


def test_update_source_interval_by_id(tmp_path: Path):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        source = add_source(
            session,
            portal="computrabajo",
            target_role="soporte_aplicaciones",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )

        updated = update_source_interval(
            session,
            source.id,
            interval_minutes=10,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )

        assert updated is not None
        assert updated.interval_minutes == 10
        assert get_source_by_id(session, source.id).interval_minutes == 10


def test_update_source_interval_rejects_below_minimum(tmp_path: Path):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        source = add_source(
            session,
            portal="computrabajo",
            target_role="soporte_aplicaciones",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )

        try:
            update_source_interval(
                session,
                source.id,
                interval_minutes=5,
                min_interval_minutes=settings.min_monitor_interval_minutes,
            )
        except ValueError as exc:
            assert "intervalo minimo permitido" in str(exc).lower()
        else:  # pragma: no cover
            raise AssertionError("Se esperaba ValueError para intervalo invalido.")


def test_update_portal_source_intervals_only_updates_active_sources(tmp_path: Path):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        active_a = add_source(
            session,
            portal="computrabajo",
            target_role="soporte_aplicaciones",
            search_url="https://computrabajo.example/jobs/1",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        active_b = add_source(
            session,
            portal="computrabajo",
            target_role="backend_junior",
            search_url="https://computrabajo.example/jobs/2",
            interval_minutes=20,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        inactive = add_source(
            session,
            portal="computrabajo",
            target_role="frontend_junior",
            search_url="https://computrabajo.example/jobs/3",
            interval_minutes=25,
            enabled=False,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        other_portal = add_source(
            session,
            portal="linkedin",
            target_role="frontend_junior",
            search_url="https://linkedin.example/jobs",
            interval_minutes=30,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )

        updated_sources = update_portal_source_intervals(
            session,
            "computrabajo",
            interval_minutes=10,
            min_interval_minutes=settings.min_monitor_interval_minutes,
            enabled_only=True,
        )

        assert {source.id for source in updated_sources} == {active_a.id, active_b.id}
        current = {source.id: source.interval_minutes for source in list_sources(session)}
        assert current[active_a.id] == 10
        assert current[active_b.id] == 10
        assert current[inactive.id] == 25
        assert current[other_portal.id] == 30


def test_handle_sources_update_interval_by_portal_prints_clear_message(tmp_path: Path, capsys):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        add_source(
            session,
            portal="computrabajo",
            target_role="soporte_aplicaciones",
            search_url="https://computrabajo.example/jobs/1",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        add_source(
            session,
            portal="computrabajo",
            target_role="backend_junior",
            search_url="https://computrabajo.example/jobs/2",
            interval_minutes=20,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        args = Namespace(id=None, portal="computrabajo", interval=10)

        code = _handle_sources_update_interval(args, session, settings, session_factory)

        output = capsys.readouterr().out
        assert code == 0
        assert "Fuentes de computrabajo actualizadas a intervalo 10 minutos: 2 fuentes." in output


def test_record_source_failure_increments_and_pauses_after_three_attempts(tmp_path: Path):
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

        first = record_source_failure(
            session,
            source,
            error="La fuente solicito captcha. Se omite esta fuente.",
            failed_at=datetime(2026, 5, 7, 10, 0, tzinfo=UTC),
        )
        second = record_source_failure(
            session,
            first,
            error="La fuente solicito captcha. Se omite esta fuente.",
            failed_at=datetime(2026, 5, 7, 11, 0, tzinfo=UTC),
        )
        third = record_source_failure(
            session,
            second,
            error="La fuente solicito captcha. Se omite esta fuente.",
            failed_at=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
        )

        assert first.id == second.id == third.id
        assert first.failure_count == 3
        assert second.failure_count == 3
        assert third.failure_count == 3
        assert ensure_utc(third.last_failed_at) == datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
        assert ensure_utc(third.paused_until) == datetime(2026, 5, 8, 12, 0, tzinfo=UTC)


def test_get_due_sources_omits_future_paused_sources(tmp_path: Path):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)

    with Session(engine) as session:
        paused = add_source(
            session,
            portal="indeed",
            target_role="frontend_junior",
            search_url="https://indeed.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        ready = add_source(
            session,
            portal="computrabajo",
            target_role="frontend_junior",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        paused.last_checked_at = datetime(2026, 5, 7, 10, 0, tzinfo=UTC)
        paused.paused_until = datetime(2026, 5, 8, 10, 0, tzinfo=UTC)
        ready.last_checked_at = datetime(2026, 5, 7, 10, 0, tzinfo=UTC)
        session.commit()

        due = get_due_sources(session, now=datetime(2026, 5, 7, 11, 0, tzinfo=UTC))

        assert [source.id for source in due] == [ready.id]


def test_unpause_source_by_id_clears_pause_and_failure_state(tmp_path: Path):
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
        source.failure_count = 4
        source.last_error = "captcha"
        source.last_failed_at = datetime(2026, 5, 7, 8, 0, tzinfo=UTC)
        source.paused_until = datetime(2026, 5, 8, 8, 0, tzinfo=UTC)
        session.commit()

        updated = unpause_source_by_id(session, source.id)

        assert updated is not None
        assert updated.failure_count == 0
        assert updated.last_error == ""
        assert updated.last_failed_at is None
        assert updated.paused_until is None


def test_unpause_sources_by_portal_clears_matching_sources(tmp_path: Path, capsys):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        source_a = add_source(
            session,
            portal="elempleo",
            target_role="backend_junior",
            search_url="https://elempleo.example/jobs/1",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        source_b = add_source(
            session,
            portal="elempleo",
            target_role="frontend_junior",
            search_url="https://elempleo.example/jobs/2",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        source_a.failure_count = 3
        source_a.last_error = "captcha"
        source_a.paused_until = datetime(2026, 5, 8, 8, 0, tzinfo=UTC)
        source_b.failure_count = 4
        source_b.last_error = "login"
        source_b.paused_until = datetime(2026, 5, 8, 9, 0, tzinfo=UTC)
        session.commit()

        updated_sources = unpause_sources_by_portal(session, "elempleo")

        assert len(updated_sources) == 2
        assert all(source.paused_until is None for source in updated_sources)
        assert all(source.failure_count == 0 for source in updated_sources)

        args = Namespace(id=None, portal="elempleo")
        code = _handle_sources_unpause(args, session, settings, session_factory)
        output = capsys.readouterr().out
        assert code == 0
        assert "Fuentes de elempleo reanudadas: 2 fuentes." in output


def test_disable_blocked_sources_disables_only_threshold_matches(tmp_path: Path, capsys):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        blocked = add_source(
            session,
            portal="elempleo",
            target_role="backend_junior",
            search_url="https://elempleo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        healthy = add_source(
            session,
            portal="computrabajo",
            target_role="backend_junior",
            search_url="https://computrabajo.example/jobs",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        blocked.failure_count = 3
        healthy.failure_count = 1
        session.commit()

        disabled = disable_blocked_sources(session)

        assert [source.id for source in disabled] == [blocked.id]
        assert get_source_by_id(session, blocked.id).enabled is False
        assert get_source_by_id(session, healthy.id).enabled is True

        args = Namespace()
        code = _handle_sources_disable_blocked(args, session, settings, session_factory)
        output = capsys.readouterr().out
        assert code == 0
        assert "No hay fuentes bloqueadas para desactivar." in output


def test_sources_test_debug_html_writes_debug_files(tmp_path: Path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        source = add_source(
            session,
            portal="elempleo",
            target_role="backend_junior",
            search_url="https://www.elempleo.com/co/ofertas-empleo/trabajo-junior-backend",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "src.jobops_assistant.cli.test_source",
            lambda settings, source: SourceTestResult(
                source=source,
                offers=[],
                error="La fuente solicito captcha. Se omite esta fuente.",
                debug_snapshot=ResponseDebugSnapshot(
                    requested_url=source.search_url,
                    status_code=200,
                    final_url=source.search_url,
                    content_type="text/html",
                    html="<html><body>Verify you are human</body></html>",
                    block_reason="turnstile visible",
                ),
            ),
        )

        code = _handle_sources_test(
            Namespace(id=source.id, debug_html=True, show_discarded=False),
            session,
            settings,
            session_factory,
        )

        output = capsys.readouterr().out
        assert code == 1
        assert "Debug HTML:" in output
        assert "Debug meta:" in output
        html_path = tmp_path / "debug" / f"source_{source.id}_{source.portal}.html"
        meta_path = tmp_path / "debug" / f"source_{source.id}_{source.portal}_meta.txt"
        assert html_path.exists()
        assert meta_path.exists()
        assert "turnstile visible" in meta_path.read_text(encoding="utf-8")


def test_sources_test_show_discarded_prints_reasons(tmp_path: Path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    engine = create_sqlite_engine(settings.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        source = add_source(
            session,
            portal="elempleo",
            target_role="backend_junior",
            search_url="https://www.elempleo.com/co/ofertas-empleo/trabajo-junior-backend",
            interval_minutes=15,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
        monkeypatch.setattr(
            "src.jobops_assistant.cli.test_source",
            lambda settings, source: SourceTestResult(
                source=source,
                offers=[],
                discarded=[
                    DiscardedJobReview(
                        job=ScrapedJob(
                            title="Senior Backend Developer",
                            company="XYZ",
                            portal="elempleo",
                            location="Bogota",
                            modality="Remoto",
                            salary="",
                            url="https://example.com/1",
                            description="Java y Spring Boot",
                            requirements="5 anos",
                            published_at=None,
                            found_at=datetime.now(UTC),
                            raw_posted_text="Hoy",
                            source_id=source.id,
                        ),
                        reasons=["contiene senior"],
                        detected_keywords=["senior", "spring boot"],
                        preliminary_score=58,
                    )
                ],
            ),
        )

        code = _handle_sources_test(
            Namespace(id=source.id, debug_html=False, show_discarded=True),
            session,
            settings,
            session_factory,
        )

        output = capsys.readouterr().out
        assert code == 0
        assert "Ofertas descartadas: 1" in output
        assert "Senior Backend Developer | XYZ | target=backend_junior" in output
        assert "razones=contiene senior" in output
        assert "keywords=senior, spring boot" in output
        assert "score=58" in output
