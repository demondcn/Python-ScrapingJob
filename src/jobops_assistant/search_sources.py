from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import JobSearchSource
from .scrapers.base_scraper import ScrapedJob
from .scrapers.registry import get_scraper
from .settings import Settings


@dataclass(slots=True)
class SourceTestResult:
    source: JobSearchSource
    offers: list[ScrapedJob]
    error: str = ""


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


def update_source_check(
    session: Session,
    source: JobSearchSource,
    *,
    checked_at: datetime | None = None,
    error: str = "",
) -> JobSearchSource:
    source.last_checked_at = ensure_utc(checked_at) or datetime.now(UTC)
    source.last_error = error.strip()
    session.commit()
    session.refresh(source)
    return source


def get_due_sources(session: Session, *, now: datetime | None = None) -> list[JobSearchSource]:
    current_time = ensure_utc(now) or datetime.now(UTC)
    due_sources: list[JobSearchSource] = []
    for source in list_sources(session, enabled=True):
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
        offers = scraper.scrape(source)
        return SourceTestResult(source=source, offers=offers)
    except Exception as exc:
        return SourceTestResult(source=source, offers=[], error=str(exc))


def _validate_interval(interval_minutes: int, min_interval_minutes: int) -> int:
    if interval_minutes < min_interval_minutes:
        raise ValueError(
            f"El intervalo minimo permitido es de {min_interval_minutes} minutos."
        )
    return interval_minutes
