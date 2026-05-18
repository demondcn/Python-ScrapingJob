from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .discarded_job_service import (
    DiscardedJobReview,
    analyze_linkedin_application_type_for_discard,
    analyze_scraped_job_for_discard,
)
from .models import JobSearchSource
from .scrapers.base_scraper import ResponseDebugSnapshot, ScrapedJob
from .scrapers.registry import get_scraper
from .settings import Settings

AUTO_PAUSE_FAILURE_THRESHOLD = 3
AUTO_PAUSE_DURATION = timedelta(hours=24)


@dataclass(slots=True)
class SourceTestResult:
    source: JobSearchSource
    offers: list[ScrapedJob]
    error: str = ""
    debug_snapshot: ResponseDebugSnapshot | None = None
    discarded: list[DiscardedJobReview] | None = None


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def add_source(
    session: Session,
    *,
    portal: str,
    target_role: str,
    search_url: str,
    keywords: str = "",
    location: str = "",
    enabled: bool = True,
    interval_minutes: int = 15,
    min_interval_minutes: int = 10,
) -> JobSearchSource:
    normalized_interval = _validate_interval(interval_minutes, min_interval_minutes)
    source = JobSearchSource(
        portal=portal.strip().lower(),
        target_role=target_role.strip(),
        search_url=search_url.strip(),
        keywords=keywords.strip(),
        location=location.strip(),
        enabled=enabled,
        interval_minutes=normalized_interval,
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def list_sources(session: Session, *, enabled: bool | None = None) -> list[JobSearchSource]:
    stmt = select(JobSearchSource).order_by(JobSearchSource.id.asc())
    if enabled is not None:
        stmt = stmt.where(JobSearchSource.enabled.is_(enabled))
    return list(session.scalars(stmt))


def get_source_by_id(session: Session, source_id: int) -> JobSearchSource | None:
    return session.get(JobSearchSource, source_id)


def set_source_enabled(session: Session, source_id: int, enabled: bool) -> JobSearchSource | None:
    source = get_source_by_id(session, source_id)
    if source is None:
        return None
    source.enabled = enabled
    session.commit()
    session.refresh(source)
    return source


def update_source_interval(
    session: Session,
    source_id: int,
    *,
    interval_minutes: int,
    min_interval_minutes: int,
) -> JobSearchSource | None:
    source = get_source_by_id(session, source_id)
    if source is None:
        return None
    source.interval_minutes = _validate_interval(interval_minutes, min_interval_minutes)
    session.commit()
    session.refresh(source)
    return source


def update_portal_source_intervals(
    session: Session,
    portal: str,
    *,
    interval_minutes: int,
    min_interval_minutes: int,
    enabled_only: bool = True,
) -> list[JobSearchSource]:
    normalized_interval = _validate_interval(interval_minutes, min_interval_minutes)
    stmt = select(JobSearchSource).where(JobSearchSource.portal == portal.strip().lower())
    if enabled_only:
        stmt = stmt.where(JobSearchSource.enabled.is_(True))
    sources = list(session.scalars(stmt))
    for source in sources:
        source.interval_minutes = normalized_interval
    session.commit()
    for source in sources:
        session.refresh(source)
    return sources


def update_source_check(
    session: Session,
    source: JobSearchSource,
    *,
    checked_at: datetime | None = None,
    error: str = "",
) -> JobSearchSource:
    source.last_checked_at = ensure_utc(checked_at) or datetime.now(UTC)
    source.last_error = error.strip()
    if not source.last_error:
        source.failure_count = 0
        source.paused_until = None
        source.last_failed_at = None
    session.commit()
    session.refresh(source)
    return source


def record_source_failure(
    session: Session,
    source: JobSearchSource,
    *,
    error: str,
    failed_at: datetime | None = None,
    pause_after: int = AUTO_PAUSE_FAILURE_THRESHOLD,
    pause_duration: timedelta = AUTO_PAUSE_DURATION,
) -> JobSearchSource:
    failure_time = ensure_utc(failed_at) or datetime.now(UTC)
    source.last_checked_at = failure_time
    source.last_failed_at = failure_time
    source.last_error = error.strip()
    source.failure_count = max(0, source.failure_count) + 1
    if source.failure_count >= pause_after:
        source.paused_until = failure_time + pause_duration
    session.commit()
    session.refresh(source)
    return source


def is_source_paused(source: JobSearchSource, *, now: datetime | None = None) -> bool:
    paused_until = ensure_utc(source.paused_until)
    if paused_until is None:
        return False
    current_time = ensure_utc(now) or datetime.now(UTC)
    return paused_until > current_time


def unpause_source_by_id(session: Session, source_id: int) -> JobSearchSource | None:
    source = get_source_by_id(session, source_id)
    if source is None:
        return None
    source.paused_until = None
    source.failure_count = 0
    source.last_error = ""
    source.last_failed_at = None
    session.commit()
    session.refresh(source)
    return source


def unpause_sources_by_portal(session: Session, portal: str) -> list[JobSearchSource]:
    stmt = select(JobSearchSource).where(JobSearchSource.portal == portal.strip().lower())
    sources = list(session.scalars(stmt))
    for source in sources:
        source.paused_until = None
        source.failure_count = 0
        source.last_error = ""
        source.last_failed_at = None
    session.commit()
    for source in sources:
        session.refresh(source)
    return sources


def disable_blocked_sources(
    session: Session,
    *,
    minimum_failures: int = AUTO_PAUSE_FAILURE_THRESHOLD,
) -> list[JobSearchSource]:
    stmt = select(JobSearchSource).where(
        JobSearchSource.failure_count >= minimum_failures,
        JobSearchSource.enabled.is_(True),
    )
    sources = list(session.scalars(stmt))
    for source in sources:
        source.enabled = False
    session.commit()
    for source in sources:
        session.refresh(source)
    return sources


def get_due_sources(session: Session, *, now: datetime | None = None) -> list[JobSearchSource]:
    current_time = ensure_utc(now) or datetime.now(UTC)
    due_sources: list[JobSearchSource] = []
    for source in list_sources(session, enabled=True):
        if is_source_paused(source, now=current_time):
            continue
        last_checked_at = ensure_utc(source.last_checked_at)
        if last_checked_at is None:
            due_sources.append(source)
            continue
        next_run = ensure_utc(last_checked_at + timedelta(minutes=source.interval_minutes))
        if next_run <= current_time:
            due_sources.append(source)
    return due_sources


def test_source(settings: Settings, source: JobSearchSource) -> SourceTestResult:
    scraper = get_scraper(source.portal, settings)
    try:
        raw_offers = scraper.scrape(source)
        offers: list[ScrapedJob] = []
        discarded: list[DiscardedJobReview] = []
        for offer in raw_offers:
            discarded_review = analyze_linkedin_application_type_for_discard(offer, settings)
            if discarded_review is None:
                discarded_review = analyze_scraped_job_for_discard(
                    offer,
                    target_role=source.target_role,
                    profile=None,
                )
            if discarded_review is None:
                offers.append(offer)
            else:
                discarded.append(discarded_review)
        return SourceTestResult(
            source=source,
            offers=offers,
            debug_snapshot=scraper.get_last_debug_snapshot(),
            discarded=discarded,
        )
    except Exception as exc:
        return SourceTestResult(
            source=source,
            offers=[],
            error=str(exc),
            debug_snapshot=scraper.get_last_debug_snapshot(),
            discarded=[],
        )


def _validate_interval(interval_minutes: int, min_interval_minutes: int) -> int:
    if interval_minutes < min_interval_minutes:
        raise ValueError(
            f"El intervalo minimo permitido es de {min_interval_minutes} minutos."
        )
    return interval_minutes
