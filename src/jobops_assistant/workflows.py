from __future__ import annotations

from sqlalchemy.orm import Session

from .job_service import mark_offer_telegram_notified
from .gmail_reader import read_recent_job_alerts
from .job_service import create_offer, list_offers, refresh_offer_match
from .profile_service import get_profile
from .settings import Settings
from .telegram_notifier import register_notification, send_job_alert


def run_daily_scan(session: Session, settings: Settings) -> list[str]:
    logs: list[str] = []
    logs.append("Iniciando lectura de alertas.")
    parsed_offers = read_recent_job_alerts(settings)
    logs.append(f"Alertas encontradas: {len(parsed_offers)}")

    profile = get_profile(session)
    for parsed in parsed_offers:
        offer = create_offer(
            session,
            title=parsed.title or "Oferta sin titulo",
            company=parsed.company,
            portal=parsed.portal,
            location=parsed.location,
            modality=parsed.modality,
            salary=parsed.salary,
            url=parsed.url or f"manual://{parsed.title}",
            description=parsed.description,
            requirements=parsed.requirements,
        )
        offer = refresh_offer_match(session, offer, profile)
        logs.append(f"Oferta procesada: {offer.title} ({offer.compatibility_score:.0f}%)")
        if offer.compatibility_score >= settings.match_threshold:
            try:
                sent, message = send_job_alert(settings, offer)
                mark_offer_telegram_notified(session, offer, notified=sent)
                register_notification(session, offer, "telegram", "sent" if sent else "skipped", message)
                logs.append(message)
            except Exception as exc:  # pragma: no cover
                message = f"Error enviando Telegram: {exc}"
                mark_offer_telegram_notified(session, offer, notified=False)
                register_notification(session, offer, "telegram", "error", message)
                logs.append(message)
    logs.append(f"Ofertas totales registradas: {len(list_offers(session))}")
    return logs
