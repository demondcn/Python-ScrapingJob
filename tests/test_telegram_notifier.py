from datetime import UTC, datetime

from src.jobops_assistant.date_utils import get_configured_timezone, parse_relative_posted_text
from src.jobops_assistant.models import JobOffer
from src.jobops_assistant.settings import Settings
from src.jobops_assistant.telegram_notifier import format_job_alert, send_job_alert_digest


def test_format_job_alert_includes_exact_publication_and_detection_times():
    offer = JobOffer(
        id=12,
        title="Soporte de Aplicaciones Junior",
        company="ABC Tecnologia",
        portal="Computrabajo",
        location="Bogota",
        modality="Hibrido",
        salary="",
        url="https://example.com/job",
        compatibility_score=84,
        match_reason="Coincide con soporte a usuarios\nCoincide con SQL",
        published_at=datetime(2026, 5, 1, 14, 30, tzinfo=UTC),
        found_at=datetime(2026, 5, 1, 20, 47, tzinfo=UTC),
    )

    message = format_job_alert(offer, target_role="soporte_aplicaciones", timezone_name="America/Bogota")

    assert "Publicada: 2026-05-01 09:30" in message
    assert "Detectada por JobOps: 2026-05-01 15:47" in message


def test_format_job_alert_uses_raw_posted_text_when_exact_datetime_is_missing():
    offer = JobOffer(
        id=15,
        title="Backend Junior",
        company="API Labs",
        portal="Indeed",
        location="Remote",
        modality="Remoto",
        salary="",
        url="https://example.com/backend",
        compatibility_score=80,
        match_reason="Coincide con backend_junior",
        raw_posted_text="hace 3 horas",
        found_at=datetime(2026, 5, 1, 20, 47, tzinfo=UTC),
    )

    message = format_job_alert(offer, target_role="backend_junior", timezone_name="UTC")

    assert "Publicada: hace 3 horas" in message
    assert "Detectada por JobOps: 2026-05-01 20:47" in message


def test_format_job_alert_handles_missing_publication_data_and_always_shows_detection():
    offer = JobOffer(
        id=16,
        title="Frontend Junior",
        company="UI Labs",
        portal="LinkedIn",
        location="Bogota",
        modality="Hibrido",
        salary="",
        url="https://example.com/frontend",
        compatibility_score=79,
        match_reason="Coincide con frontend_junior",
    )

    message = format_job_alert(offer, target_role="frontend_junior", timezone_name="UTC")

    assert "Publicada: No disponible" in message
    assert "Detectada por JobOps:" in message


def test_parse_relative_posted_text_hours():
    now = datetime(2026, 5, 1, 20, 0, tzinfo=UTC)
    parsed = parse_relative_posted_text("hace 3 horas", now=now)
    assert parsed == datetime(2026, 5, 1, 17, 0, tzinfo=UTC)


def test_parse_relative_posted_text_days():
    now = datetime(2026, 5, 3, 20, 0, tzinfo=UTC)
    parsed = parse_relative_posted_text("hace 2 días", now=now)
    assert parsed == datetime(2026, 5, 1, 20, 0, tzinfo=UTC)


def test_parse_relative_posted_text_yesterday():
    now = datetime(2026, 5, 3, 20, 0, tzinfo=UTC)
    parsed = parse_relative_posted_text("ayer", now=now)
    assert parsed == datetime(2026, 5, 2, 20, 0, tzinfo=UTC)


def test_parse_relative_posted_text_today():
    now = datetime(2026, 5, 3, 20, 0, tzinfo=UTC)
    parsed = parse_relative_posted_text("hoy", now=now)
    assert parsed == now


def test_get_configured_timezone_handles_utc_without_zoneinfo():
    tz = get_configured_timezone("UTC")
    assert tz is not None
    assert datetime(2026, 5, 1, 12, 0, tzinfo=UTC).astimezone(tz).utcoffset().total_seconds() == 0


def test_get_configured_timezone_handles_invalid_timezone_with_fallback():
    tz = get_configured_timezone("Invalid/Timezone")
    assert tz is not None
    assert datetime(2026, 5, 1, 12, 0, tzinfo=UTC).astimezone(tz).utcoffset().total_seconds() == 0


def test_get_configured_timezone_supports_america_bogota():
    tz = get_configured_timezone("America/Bogota")
    localized = datetime(2026, 5, 1, 12, 0, tzinfo=UTC).astimezone(tz)
    assert localized.utcoffset().total_seconds() in (-18000, -18000.0)


def test_format_job_alert_never_raises_for_invalid_timezone():
    offer = JobOffer(
        id=18,
        title="QA Junior",
        company="Test Labs",
        portal="Torre",
        location="Bogota",
        modality="Remoto",
        salary="",
        url="https://example.com/qa",
        compatibility_score=70,
        match_reason="Coincide con qa_junior",
    )

    message = format_job_alert(offer, target_role="qa_junior", timezone_name="Invalid/Timezone")

    assert "Publicada: No disponible" in message
    assert "Detectada por JobOps:" in message


def test_send_job_alert_digest_limits_jobs_and_mentions_additional(monkeypatch, tmp_path):
    sent_messages: list[str] = []
    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        match_threshold=50,
        telegram_bot_token="token",
        telegram_chat_id="chat",
        gmail_email="",
        gmail_app_password="",
        scraper_timeout=5,
        scraper_user_agent="agent",
        max_results_per_source=25,
        min_monitor_interval_minutes=10,
        telegram_digest_max_jobs=2,
        telegram_max_message_chars=3500,
        templates_dir=tmp_path / "templates",
        generated_dir=tmp_path / "generated",
        timezone_name="UTC",
    )
    offers = [
        JobOffer(id=1, title="A", company="C1", portal="P", location="L", modality="", salary="", url="https://example.com/1", compatibility_score=90),
        JobOffer(id=2, title="B", company="C2", portal="P", location="L", modality="", salary="", url="https://example.com/2", compatibility_score=80),
        JobOffer(id=3, title="C", company="C3", portal="P", location="L", modality="", salary="", url="https://example.com/3", compatibility_score=70),
    ]

    monkeypatch.setattr(
        "src.jobops_assistant.telegram_notifier._post_telegram_message",
        lambda settings, message: sent_messages.append(message),
    )

    sent, message, delivered = send_job_alert_digest(offers, settings)

    assert sent is True
    assert "2 ofertas" in message
    assert [offer.id for offer in delivered] == [1, 2]
    assert len(sent_messages) == 1
    assert "Hay 1 ofertas adicionales guardadas" in sent_messages[0]
    assert "1. A" in sent_messages[0]
    assert "2. B" in sent_messages[0]
    assert "3. C" not in sent_messages[0]


def test_send_job_alert_digest_limit_zero_sends_all_jobs(monkeypatch, tmp_path):
    sent_messages: list[str] = []
    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        match_threshold=50,
        telegram_bot_token="token",
        telegram_chat_id="chat",
        gmail_email="",
        gmail_app_password="",
        scraper_timeout=5,
        scraper_user_agent="agent",
        max_results_per_source=25,
        min_monitor_interval_minutes=10,
        telegram_digest_max_jobs=0,
        telegram_max_message_chars=20000,
        templates_dir=tmp_path / "templates",
        generated_dir=tmp_path / "generated",
        timezone_name="UTC",
    )
    offers = [
        JobOffer(
            id=index,
            title=f"Oferta {index}",
            company="Empresa",
            portal="Portal",
            location="Bogota",
            modality="Remoto",
            salary="",
            url=f"https://example.com/{index}",
            compatibility_score=100 - index,
        )
        for index in range(1, 26)
    ]

    monkeypatch.setattr(
        "src.jobops_assistant.telegram_notifier._post_telegram_message",
        lambda settings, message: sent_messages.append(message),
    )

    sent, message, delivered = send_job_alert_digest(offers, settings)

    assert sent is True
    assert message == "digest enviado con 25 ofertas"
    assert len(delivered) == 25
    assert [offer.id for offer in delivered] == list(range(1, 26))
    assert len(sent_messages) == 1
    assert "25. Oferta 25" in sent_messages[0]


def test_send_job_alert_digest_positive_limit_caps_jobs(monkeypatch, tmp_path):
    sent_messages: list[str] = []
    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        match_threshold=50,
        telegram_bot_token="token",
        telegram_chat_id="chat",
        gmail_email="",
        gmail_app_password="",
        scraper_timeout=5,
        scraper_user_agent="agent",
        max_results_per_source=25,
        min_monitor_interval_minutes=10,
        telegram_digest_max_jobs=10,
        telegram_max_message_chars=20000,
        templates_dir=tmp_path / "templates",
        generated_dir=tmp_path / "generated",
        timezone_name="UTC",
    )
    offers = [
        JobOffer(
            id=index,
            title=f"Oferta {index}",
            company="Empresa",
            portal="Portal",
            location="Bogota",
            modality="Remoto",
            salary="",
            url=f"https://example.com/{index}",
            compatibility_score=100 - index,
        )
        for index in range(1, 26)
    ]

    monkeypatch.setattr(
        "src.jobops_assistant.telegram_notifier._post_telegram_message",
        lambda settings, message: sent_messages.append(message),
    )

    sent, message, delivered = send_job_alert_digest(offers, settings)

    assert sent is True
    assert message == "digest enviado con 10 ofertas"
    assert len(delivered) == 10
    assert [offer.id for offer in delivered] == list(range(1, 11))
    assert len(sent_messages) == 1
    assert "Hay 15 ofertas adicionales guardadas" in sent_messages[0]
    assert "10. Oferta 10" in sent_messages[0]
    assert "11. Oferta 11" not in sent_messages[0]


def test_send_job_alert_digest_splits_long_messages(monkeypatch, tmp_path):
    sent_messages: list[str] = []
    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        match_threshold=50,
        telegram_bot_token="token",
        telegram_chat_id="chat",
        gmail_email="",
        gmail_app_password="",
        scraper_timeout=5,
        scraper_user_agent="agent",
        max_results_per_source=25,
        min_monitor_interval_minutes=10,
        telegram_digest_max_jobs=10,
        telegram_max_message_chars=450,
        templates_dir=tmp_path / "templates",
        generated_dir=tmp_path / "generated",
        timezone_name="UTC",
    )
    offers = [
        JobOffer(id=1, title="Oferta Muy Larga 1", company="Empresa 1", portal="Portal", location="Bogota", modality="Remoto", salary="", url="https://example.com/1", compatibility_score=95),
        JobOffer(id=2, title="Oferta Muy Larga 2", company="Empresa 2", portal="Portal", location="Bogota", modality="Remoto", salary="", url="https://example.com/2", compatibility_score=94),
        JobOffer(id=3, title="Oferta Muy Larga 3", company="Empresa 3", portal="Portal", location="Bogota", modality="Remoto", salary="", url="https://example.com/3", compatibility_score=93),
    ]

    monkeypatch.setattr(
        "src.jobops_assistant.telegram_notifier._post_telegram_message",
        lambda settings, message: sent_messages.append(message),
    )

    sent, message, delivered = send_job_alert_digest(offers, settings)

    assert sent is True
    assert message.startswith("digest enviado con 3 ofertas en ")
    assert len(delivered) == 3
    assert len(sent_messages) >= 2
    assert any("Parte 1/" in message for message in sent_messages)


def test_send_job_alert_digest_returns_false_if_any_part_fails(monkeypatch, tmp_path):
    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        match_threshold=50,
        telegram_bot_token="token",
        telegram_chat_id="chat",
        gmail_email="",
        gmail_app_password="",
        scraper_timeout=5,
        scraper_user_agent="agent",
        max_results_per_source=25,
        min_monitor_interval_minutes=10,
        telegram_digest_max_jobs=10,
        telegram_max_message_chars=450,
        templates_dir=tmp_path / "templates",
        generated_dir=tmp_path / "generated",
        timezone_name="UTC",
    )
    offers = [
        JobOffer(id=1, title="Oferta 1", company="Empresa 1", portal="Portal", location="Bogota", modality="Remoto", salary="", url="https://example.com/1", compatibility_score=95),
        JobOffer(id=2, title="Oferta 2", company="Empresa 2", portal="Portal", location="Bogota", modality="Remoto", salary="", url="https://example.com/2", compatibility_score=94),
        JobOffer(id=3, title="Oferta 3", company="Empresa 3", portal="Portal", location="Bogota", modality="Remoto", salary="", url="https://example.com/3", compatibility_score=93),
    ]
    calls = {"count": 0}

    def _fail_on_second(settings, message):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("telegram failed")

    monkeypatch.setattr("src.jobops_assistant.telegram_notifier._post_telegram_message", _fail_on_second)

    sent, message, delivered = send_job_alert_digest(offers, settings)

    assert sent is False
    assert "Error enviando digest por Telegram" in message
    assert 0 < len(delivered) < len(offers)
