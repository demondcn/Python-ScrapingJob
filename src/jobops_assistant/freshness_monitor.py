from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .application_types import LINKEDIN_EASY_APPLY
from .discarded_job_service import (
    analyze_linkedin_application_type_for_discard,
    analyze_scraped_job_for_discard,
    build_url_hash,
    remove_discarded_job_by_url_hash,
    upsert_discarded_job,
)
from .job_service import (
    create_offer,
    find_possible_duplicate,
    get_offer_by_normalized_url,
    get_offer_by_url,
    list_pending_alert_offers,
    mark_offer_telegram_notified,
    refresh_offer_match,
)
from .models import JobOffer, JobSearchSource, JobSeenHash
from .profile_service import get_profile
from .scrapers.base_scraper import (
    CaptchaRequiredError,
    LoginRequiredError,
    ScrapedJob,
    SourceBlockedError,
)
from .scrapers.registry import get_scraper
from .search_sources import (
    ensure_utc,
    get_due_sources,
    is_source_paused,
    list_sources,
    record_source_failure,
    update_source_check,
)
from .settings import Settings
from .telegram_notifier import register_notification, send_job_alert_digest


@dataclass(slots=True)
class SourceRunStats:
    portal: str
    source_id: int
    found: int = 0
    created: int = 0
    duplicates: int = 0
    discarded: int = 0
    pending_alerts: int = 0
    retried_alerts: int = 0
    queued_alerts: int = 0


@dataclass(slots=True)
class IngestedJob:
    offer: JobOffer
    created: bool


@dataclass(slots=True)
class DigestSendOutcome:
    attempted_offer_ids: set[int]
    delivered_offer_ids: set[int]
    sent: bool
    message: str


def run_fresh_monitor(
    session: Session,
    settings: Settings,
    *,
    force_all: bool = True,
    notify_pending: bool = False,
) -> list[str]:
    logs: list[str] = []
    enabled_sources = list_sources(session, enabled=True)
    if force_all:
        sources = enabled_sources
    else:
        current_time = datetime.now(UTC)
        due_sources = get_due_sources(session, now=current_time)
        paused_sources = [source for source in enabled_sources if is_source_paused(source, now=current_time)]
        due_ids = {source.id for source in due_sources}
        sources = due_sources + [source for source in paused_sources if source.id not in due_ids]

    if not sources:
        if force_all:
            return ["No hay fuentes habilitadas para revisar."]
        if enabled_sources:
            return ["No hay fuentes pendientes por revisar en este ciclo."]
        return ["No hay fuentes habilitadas para revisar."]

    profile = get_profile(session)
    digest_queue: dict[int, tuple[JobOffer, str | None]] = {}
    notify_after_each_source = getattr(settings, "notify_after_each_source", False)
    for source in sources:
        if is_source_paused(source, now=datetime.now(UTC)):
            logs.append(_build_paused_source_log(source))
            continue
        logs.append(f"[{source.portal}] Revisando fuente {source.id}: {source.search_url}")
        checked_at = datetime.now(UTC)
        try:
            scraper = get_scraper(source.portal, settings)
            jobs = scraper.scrape(source)
            stats = SourceRunStats(portal=source.portal, source_id=source.id, found=len(jobs))
            processed_offer_ids: set[int] = set()
            queued_offer_ids: set[int] = set()
            source_digest_queue: dict[int, tuple[JobOffer, str | None]] = {}
            for job in jobs:
                application_review = analyze_linkedin_application_type_for_discard(job, settings)
                if application_review is not None:
                    upsert_discarded_job(
                        session,
                        portal=source.portal,
                        source_id=source.id,
                        target_role=source.target_role,
                        source_url=source.search_url,
                        review=application_review,
                    )
                    stats.discarded += 1
                    continue
                discarded_review = analyze_scraped_job_for_discard(
                    job,
                    target_role=source.target_role,
                    profile=profile,
                )
                if discarded_review is not None:
                    upsert_discarded_job(
                        session,
                        portal=source.portal,
                        source_id=source.id,
                        target_role=source.target_role,
                        source_url=source.search_url,
                        review=discarded_review,
                    )
                    stats.discarded += 1
                    continue
                ingested = _ingest_scraped_job(session, source, job, profile)
                if ingested.offer.id is not None:
                    processed_offer_ids.add(ingested.offer.id)
                if ingested.created:
                    stats.created += 1
                    if _should_notify_offer(ingested.offer, job, settings):
                        _queue_offer_for_cycle(
                            digest_queue,
                            source_digest_queue,
                            ingested.offer,
                            source.target_role,
                            notify_after_each_source=notify_after_each_source,
                        )
                        if ingested.offer.id is not None:
                            queued_offer_ids.add(ingested.offer.id)
                else:
                    stats.duplicates += 1
                    if _is_pending_alert_offer(ingested.offer, settings):
                        stats.pending_alerts += 1
                        _queue_offer_for_cycle(
                            digest_queue,
                            source_digest_queue,
                            ingested.offer,
                            source.target_role,
                            notify_after_each_source=notify_after_each_source,
                        )
                        if ingested.offer.id is not None:
                            queued_offer_ids.add(ingested.offer.id)

            if notify_pending:
                pending_offers = list_pending_alert_offers(
                    session,
                    threshold=settings.match_threshold,
                    source_id=source.id,
                )
                for offer in pending_offers:
                    if offer.id in processed_offer_ids:
                        continue
                    if not _offer_passes_linkedin_application_filter(offer, settings):
                        continue
                    stats.pending_alerts += 1
                    _queue_offer_for_cycle(
                        digest_queue,
                        source_digest_queue,
                        offer,
                        source.target_role,
                        notify_after_each_source=notify_after_each_source,
                    )
                    if offer.id is not None:
                        queued_offer_ids.add(offer.id)
                        stats.retried_alerts += 1

            stats.queued_alerts = len(queued_offer_ids)
            update_source_check(session, source, checked_at=checked_at, error="")
            logs.append(
                f"[{source.portal}] encontradas={stats.found} nuevas={stats.created} "
                f"duplicados={stats.duplicates} descartadas={stats.discarded} pending_alerts={stats.pending_alerts} "
                f"queued_alerts={stats.queued_alerts}"
            )
            if notify_after_each_source:
                immediate_logs, outcome = _send_immediate_digest_for_source(
                    session,
                    settings,
                    source_digest_queue,
                    source_id=source.id,
                )
                logs.extend(immediate_logs)
                if not outcome.sent:
                    _remove_digest_queue_items(digest_queue, outcome.attempted_offer_ids)
        except (CaptchaRequiredError, LoginRequiredError, SourceBlockedError) as exc:
            failed_source = record_source_failure(session, source, error=str(exc), failed_at=checked_at)
            if failed_source.paused_until is not None:
                logs.append(_build_paused_source_log(failed_source))
            else:
                logs.append(
                    f"[{source.portal}] Error: {exc} "
                    f"(fallos={failed_source.failure_count}/{3})"
                )
        except Exception as exc:
            update_source_check(session, source, checked_at=checked_at, error=str(exc))
            logs.append(f"[{source.portal}] Error: {exc}")

    if notify_after_each_source:
        logs.extend(_send_final_digest_after_immediate(session, settings, digest_queue))
    else:
        logs.extend(_send_digest_for_cycle(session, settings, digest_queue))
    return logs


def retry_pending_alerts(
    session: Session,
    settings: Settings,
    *,
    portal: str | None = None,
    source_id: int | None = None,
    target_role: str | None = None,
) -> list[str]:
    offers = list_pending_alert_offers(
        session,
        threshold=settings.match_threshold,
        portal=portal,
        source_id=source_id,
    )
    if not offers:
        return ["No hay ofertas pendientes de alerta."]
    digest_queue = {
        offer.id: (offer, target_role)
        for offer in offers
        if offer.id is not None
    }
    return _send_digest_for_cycle(session, settings, digest_queue)


def is_scraped_job_fresh(job: ScrapedJob, *, now: datetime | None = None) -> bool:
    current_time = ensure_utc(now) or datetime.now(UTC)
    published_at = ensure_utc(job.published_at)
    if published_at is not None:
        return current_time - published_at <= timedelta(hours=24)

    raw_text = _normalize_text(job.raw_posted_text)
    if any(token in raw_text for token in ("today", "hoy", "new", "just posted", "reciã©n", "recien publicada", "publicada hoy")):
        return True
    if re.search(r"(?:hace|ago)\s+\d+\s+(?:hora|horas|hour|hours|minuto|minutos|minute|minutes)", raw_text):
        return True
    return True


def get_seen_hash(session: Session, url_hash: str) -> JobSeenHash | None:
    return session.scalar(select(JobSeenHash).where(JobSeenHash.url_hash == url_hash))


def record_seen_hash(session: Session, *, url_hash: str, normalized_url: str, portal: str) -> JobSeenHash:
    existing = get_seen_hash(session, url_hash)
    if existing is not None:
        existing.last_seen_at = datetime.now(UTC)
        session.commit()
        session.refresh(existing)
        return existing

    record = JobSeenHash(
        url_hash=url_hash,
        normalized_url=normalized_url,
        portal=portal,
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record
def _ingest_scraped_job(
    session: Session,
    source: JobSearchSource,
    job: ScrapedJob,
    profile,
) -> IngestedJob:
    normalized_url = job.url
    url_hash = build_url_hash(normalized_url)
    existing_by_url = get_offer_by_url(session, normalized_url)
    existing_by_normalized = get_offer_by_normalized_url(session, normalized_url)
    possible_duplicate = find_possible_duplicate(
        session,
        title=job.title,
        company=job.company,
        portal=job.portal,
    )
    already_existing = existing_by_url or existing_by_normalized or possible_duplicate
    existing_offer = create_offer(
        session,
        title=job.title,
        company=job.company,
        portal=job.portal,
        location=job.location,
        modality=job.modality,
        salary=job.salary,
        url=normalized_url,
        description=job.description,
        requirements=job.requirements,
        published_at=job.published_at,
        found_at=job.found_at,
        raw_posted_text=job.raw_posted_text,
        application_type=job.application_type,
        normalized_url=normalized_url,
        url_hash=url_hash,
        source_id=source.id,
    )
    created = already_existing is None

    if not created:
        duplicate_offer = possible_duplicate or existing_offer
        duplicate_offer.notes = _append_monitor_note(
            duplicate_offer.notes,
            f"Posible duplicado detectado desde {source.portal}: {normalized_url}",
        )
        session.commit()
        session.refresh(duplicate_offer)
        refresh_offer_match(session, duplicate_offer, profile)
        remove_discarded_job_by_url_hash(session, url_hash)
        record_seen_hash(session, url_hash=url_hash, normalized_url=normalized_url, portal=source.portal)
        return IngestedJob(offer=duplicate_offer, created=False)

    refresh_offer_match(session, existing_offer, profile)
    remove_discarded_job_by_url_hash(session, url_hash)
    record_seen_hash(session, url_hash=url_hash, normalized_url=normalized_url, portal=source.portal)
    return IngestedJob(offer=existing_offer, created=True)


def _send_digest_for_cycle(
    session: Session,
    settings: Settings,
    digest_queue: dict[int, tuple[JobOffer, str | None]],
) -> list[str]:
    outcome = _send_digest_queue(session, settings, digest_queue)
    if not outcome.attempted_offer_ids:
        return []
    if outcome.sent:
        if outcome.message.casefold().startswith("digest enviado con "):
            return [f"[telegram] {outcome.message[:1].lower()}{outcome.message[1:]}"]
        return [f"[telegram] digest enviado con {len(outcome.delivered_offer_ids)} ofertas"]
    return [f"[telegram] {outcome.message}"]


def _send_immediate_digest_for_source(
    session: Session,
    settings: Settings,
    digest_queue: dict[int, tuple[JobOffer, str | None]],
    *,
    source_id: int,
) -> tuple[list[str], DigestSendOutcome]:
    outcome = _send_digest_queue(session, settings, digest_queue)
    if not outcome.attempted_offer_ids:
        return [f"[telegram] sin ofertas nuevas para fuente {source_id}"], outcome
    if outcome.sent:
        return [f"[telegram] envío inmediato con {len(outcome.delivered_offer_ids)} ofertas desde fuente {source_id}"], outcome
    return [f"[telegram] fallo envio inmediato para fuente {source_id}: {outcome.message}"], outcome


def _send_final_digest_after_immediate(
    session: Session,
    settings: Settings,
    digest_queue: dict[int, tuple[JobOffer, str | None]],
) -> list[str]:
    if not _get_notifiable_queue_items(digest_queue, settings):
        return ["[telegram] no hay pendientes al final del ciclo"]
    return _send_digest_for_cycle(session, settings, digest_queue)


def _send_digest_queue(
    session: Session,
    settings: Settings,
    digest_queue: dict[int, tuple[JobOffer, str | None]],
) -> DigestSendOutcome:
    if not digest_queue:
        return DigestSendOutcome(set(), set(), False, "")
    queue_items = _get_notifiable_queue_items(digest_queue, settings)
    if not queue_items:
        return DigestSendOutcome(set(), set(), False, "")
    attempted_offer_ids = {
        offer.id
        for offer, _ in queue_items
        if offer.id is not None
    }
    offers = [offer for offer, _ in queue_items]
    sent, message, delivered_offers = send_job_alert_digest(offers, settings)
    delivered_ids = {offer.id for offer in delivered_offers if offer.id is not None}
    for offer, _ in queue_items:
        delivered = offer.id in delivered_ids
        mark_offer_telegram_notified(session, offer, notified=delivered)
        status = "sent" if delivered else ("pending" if delivered_offers else "error")
        register_notification(session, offer, "telegram", status, message)
    return DigestSendOutcome(attempted_offer_ids, delivered_ids, sent, message)


def _get_notifiable_queue_items(
    digest_queue: dict[int, tuple[JobOffer, str | None]],
    settings: Settings,
) -> list[tuple[JobOffer, str | None]]:
    if not digest_queue:
        return []
    queue_items = [
        (offer, target_role)
        for offer, target_role in digest_queue.values()
        if not offer.telegram_notified and _offer_passes_linkedin_application_filter(offer, settings)
    ]
    return queue_items


def _remove_digest_queue_items(
    digest_queue: dict[int, tuple[JobOffer, str | None]],
    offer_ids: set[int],
) -> None:
    for offer_id in offer_ids:
        digest_queue.pop(offer_id, None)


def _queue_offer_for_cycle(
    cycle_digest_queue: dict[int, tuple[JobOffer, str | None]],
    source_digest_queue: dict[int, tuple[JobOffer, str | None]],
    offer: JobOffer,
    target_role: str | None,
    *,
    notify_after_each_source: bool,
) -> None:
    if notify_after_each_source:
        _queue_digest_offer(source_digest_queue, offer, target_role)
    _queue_digest_offer(cycle_digest_queue, offer, target_role)


def _queue_digest_offer(
    digest_queue: dict[int, tuple[JobOffer, str | None]],
    offer: JobOffer,
    target_role: str | None,
) -> None:
    if offer.id is None:
        return
    digest_queue.setdefault(offer.id, (offer, target_role))


def _is_pending_alert_offer(offer: JobOffer, settings: Settings) -> bool:
    return (
        (not offer.telegram_notified)
        and offer.compatibility_score >= settings.match_threshold
        and _offer_passes_linkedin_application_filter(offer, settings)
    )


def _should_notify_offer(offer: JobOffer, job: ScrapedJob, settings: Settings) -> bool:
    return (
        (not offer.telegram_notified)
        and is_scraped_job_fresh(job)
        and offer.compatibility_score >= settings.match_threshold
        and _offer_passes_linkedin_application_filter(offer, settings)
    )


def _offer_passes_linkedin_application_filter(offer: JobOffer, settings: Settings) -> bool:
    if not getattr(settings, "linkedin_only_easy_apply", False):
        return True
    if (offer.portal or "").casefold() != "linkedin_selenium":
        return True
    return offer.application_type == LINKEDIN_EASY_APPLY


def _append_monitor_note(current: str, note: str) -> str:
    if not current:
        return note
    if note in current:
        return current
    return f"{current}\n{note}"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _build_paused_source_log(source: JobSearchSource) -> str:
    paused_until = ensure_utc(source.paused_until)
    paused_label = paused_until.strftime("%Y-%m-%d %H:%M") if paused_until else "desconocido"
    reason = source.last_error or "bloqueo repetido"
    return f"[{source.portal}] fuente {source.id} pausada hasta {paused_label} por {reason}"
