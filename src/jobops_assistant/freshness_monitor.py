from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .job_service import create_offer, find_possible_duplicate, refresh_offer_match
from .models import JobOffer, JobSearchSource, JobSeenHash
from .profile_service import get_profile
from .scrapers.base_scraper import ScrapedJob
from .scrapers.registry import get_scraper
from .search_sources import ensure_utc, get_due_sources, list_sources, update_source_check
from .settings import Settings
from .telegram_notifier import register_notification, send_job_alert


@dataclass(slots=True)
class SourceRunStats:
    portal: str
    source_id: int
    found: int = 0
    created: int = 0
    duplicates: int = 0
    alerts_sent: int = 0


def run_fresh_monitor(session: Session, settings: Settings, *, force_all: bool = True) -> list[str]:
    logs: list[str] = []
    sources = list_sources(session, enabled=True) if force_all else get_due_sources(session)
    if not sources:
        if force_all:
            return ["No hay fuentes habilitadas para revisar."]
        if list_sources(session, enabled=True):
            return ["No hay fuentes pendientes por revisar en este ciclo."]
        return ["No hay fuentes habilitadas para revisar."]

    profile = get_profile(session)
    for source in sources:
        logs.append(f"[{source.portal}] Revisando fuente {source.id}: {source.search_url}")
        checked_at = datetime.now(UTC)
        try:
            scraper = get_scraper(source.portal, settings)
            jobs = scraper.scrape(source)
            stats = SourceRunStats(portal=source.portal, source_id=source.id, found=len(jobs))
            for job in jobs:
                offer = _ingest_scraped_job(
                    session,
                    settings,
                    source,
                    job,
                    profile,
                )
                if offer is not None:
                    stats.created += 1
                    if _should_notify_offer(offer, job, settings):
                        try:
                            sent, message = send_job_alert(settings, offer, target_role=source.target_role)
                            register_notification(session, offer, "telegram", "sent" if sent else "skipped", message)
                            if sent:
                                stats.alerts_sent += 1
                            logs.append(f"[{source.portal}] {message}")
                        except Exception as exc:  # pragma: no cover
                            message = f"Error enviando Telegram: {exc}"
                            register_notification(session, offer, "telegram", "error", message)
                            logs.append(f"[{source.portal}] {message}")
                else:
                    stats.duplicates += 1
            update_source_check(session, source, checked_at=checked_at, error="")
            logs.append(
                f"[{source.portal}] encontradas={stats.found} nuevas={stats.created} "
                f"duplicados={stats.duplicates} alertas={stats.alerts_sent}"
            )
        except Exception as exc:
            update_source_check(session, source, checked_at=checked_at, error=str(exc))
            logs.append(f"[{source.portal}] Error: {exc}")
    return logs


def is_scraped_job_fresh(job: ScrapedJob, *, now: datetime | None = None) -> bool:
    current_time = ensure_utc(now) or datetime.now(UTC)
    published_at = ensure_utc(job.published_at)
    if published_at is not None:
        return current_time - published_at <= timedelta(hours=24)

    raw_text = _normalize_text(job.raw_posted_text)
    if any(token in raw_text for token in ("today", "hoy", "new", "just posted", "recién", "recien publicada", "publicada hoy")):
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


def build_url_hash(normalized_url: str) -> str:
    return sha256(normalized_url.encode("utf-8")).hexdigest()


def _ingest_scraped_job(
    session: Session,
    settings: Settings,
    source: JobSearchSource,
    job: ScrapedJob,
    profile,
) -> JobOffer | None:
    normalized_url = job.url
    url_hash = build_url_hash(normalized_url)
    if get_seen_hash(session, url_hash):
        record_seen_hash(session, url_hash=url_hash, normalized_url=normalized_url, portal=source.portal)
        return None

    possible_duplicate = find_possible_duplicate(
        session,
        title=job.title,
        company=job.company,
        portal=job.portal,
    )
    if possible_duplicate is not None:
        possible_duplicate.notes = _append_monitor_note(
            possible_duplicate.notes,
            f"Posible duplicado detectado desde {source.portal}: {normalized_url}",
        )
        session.commit()
        record_seen_hash(session, url_hash=url_hash, normalized_url=normalized_url, portal=source.portal)
        return None

    offer = create_offer(
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
        normalized_url=normalized_url,
        url_hash=url_hash,
        source_id=source.id,
    )
    refresh_offer_match(session, offer, profile)
    record_seen_hash(session, url_hash=url_hash, normalized_url=normalized_url, portal=source.portal)
    return offer


def _should_notify_offer(offer: JobOffer, job: ScrapedJob, settings: Settings) -> bool:
    return is_scraped_job_fresh(job) and offer.compatibility_score >= settings.match_threshold


def _append_monitor_note(current: str, note: str) -> str:
    if not current:
        return note
    if note in current:
        return current
    return f"{current}\n{note}"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()
