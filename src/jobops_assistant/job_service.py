from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .matcher import calculate_match
from .models import CandidateProfile, GeneratedDocument, JobOffer, JobSeenHash, Notification


def create_offer(
    session: Session,
    *,
    title: str,
    company: str,
    portal: str,
    location: str,
    modality: str,
    salary: str,
    url: str,
    description: str,
    requirements: str,
    notes: str = "",
    published_at: datetime | None = None,
    found_at: datetime | None = None,
    raw_posted_text: str = "",
    normalized_url: str = "",
    url_hash: str = "",
    source_id: int | None = None,
) -> JobOffer:
    existing = get_offer_by_url(session, url)
    if existing is None and normalized_url:
        existing = get_offer_by_normalized_url(session, normalized_url)
    if existing:
        return _merge_offer_fields(
            session,
            existing,
            company=company,
            portal=portal,
            location=location,
            modality=modality,
            salary=salary,
            description=description,
            requirements=requirements,
            notes=notes,
            published_at=published_at,
            found_at=found_at,
            raw_posted_text=raw_posted_text,
            normalized_url=normalized_url,
            url_hash=url_hash,
            source_id=source_id,
        )

    offer = JobOffer(
        title=title,
        company=company,
        portal=portal,
        location=location,
        modality=modality,
        salary=salary,
        url=url,
        description=description,
        requirements=requirements,
        notes=notes,
        published_at=published_at,
        found_at=found_at or datetime.now(UTC),
        raw_posted_text=raw_posted_text,
        normalized_url=normalized_url or url,
        url_hash=url_hash,
        source_id=source_id,
    )
    session.add(offer)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = get_offer_by_url(session, url)
        if existing is None and normalized_url:
            existing = get_offer_by_normalized_url(session, normalized_url)
        if existing is None:
            raise
        return _merge_offer_fields(
            session,
            existing,
            company=company,
            portal=portal,
            location=location,
            modality=modality,
            salary=salary,
            description=description,
            requirements=requirements,
            notes=notes,
            published_at=published_at,
            found_at=found_at,
            raw_posted_text=raw_posted_text,
            normalized_url=normalized_url,
            url_hash=url_hash,
            source_id=source_id,
        )
    session.refresh(offer)
    return offer


def list_offers(session: Session, portal: str | None = None) -> list[JobOffer]:
    stmt = select(JobOffer)
    if portal:
        stmt = stmt.where(func.lower(JobOffer.portal) == portal.lower())
    stmt = stmt.order_by(func.coalesce(JobOffer.found_at, JobOffer.created_at).desc())
    return list(session.scalars(stmt))


def list_fresh_offers(session: Session, *, portal: str | None = None, hours: int = 24) -> list[JobOffer]:
    threshold = datetime.now(UTC) - timedelta(hours=hours)
    stmt = select(JobOffer).where(
        or_(
            JobOffer.found_at >= threshold,
            JobOffer.published_at >= threshold,
            JobOffer.created_at >= threshold,
        )
    )
    if portal:
        stmt = stmt.where(func.lower(JobOffer.portal) == portal.lower())
    stmt = stmt.order_by(func.coalesce(JobOffer.found_at, JobOffer.created_at).desc())
    return list(session.scalars(stmt))


def list_pending_alert_offers(
    session: Session,
    *,
    threshold: int,
    portal: str | None = None,
    source_id: int | None = None,
) -> list[JobOffer]:
    stmt = select(JobOffer).where(
        JobOffer.telegram_notified.is_(False),
        JobOffer.compatibility_score >= threshold,
    )
    if portal:
        stmt = stmt.where(func.lower(JobOffer.portal) == portal.lower())
    if source_id is not None:
        stmt = stmt.where(JobOffer.source_id == source_id)
    stmt = stmt.order_by(JobOffer.compatibility_score.desc(), func.coalesce(JobOffer.found_at, JobOffer.created_at).desc())
    return list(session.scalars(stmt))


def get_offer_by_id(session: Session, offer_id: int) -> JobOffer | None:
    return session.get(JobOffer, offer_id)


def get_offer_by_url(session: Session, url: str) -> JobOffer | None:
    return session.scalar(select(JobOffer).where(JobOffer.url == url))


def get_offer_by_normalized_url(session: Session, normalized_url: str) -> JobOffer | None:
    return session.scalar(select(JobOffer).where(JobOffer.normalized_url == normalized_url))


def find_possible_duplicate(session: Session, *, title: str, company: str, portal: str) -> JobOffer | None:
    return session.scalar(
        select(JobOffer).where(
            func.lower(JobOffer.title) == title.lower(),
            func.lower(JobOffer.company) == company.lower(),
            func.lower(JobOffer.portal) == portal.lower(),
        )
    )


def update_offer_status(session: Session, offer_id: int, status: str) -> JobOffer | None:
    offer = get_offer_by_id(session, offer_id)
    if offer is None:
        return None
    offer.status = status
    session.commit()
    session.refresh(offer)
    return offer


def update_offer_notes(session: Session, offer_id: int, notes: str) -> JobOffer | None:
    offer = get_offer_by_id(session, offer_id)
    if offer is None:
        return None
    offer.notes = notes
    session.commit()
    session.refresh(offer)
    return offer


def mark_offer_telegram_notified(
    session: Session,
    offer: JobOffer,
    *,
    notified: bool,
    notified_at: datetime | None = None,
) -> JobOffer:
    offer.telegram_notified = notified
    offer.telegram_notified_at = (notified_at or datetime.now(UTC)) if notified else None
    session.commit()
    session.refresh(offer)
    return offer


def refresh_offer_match(session: Session, offer: JobOffer, profile: CandidateProfile | None) -> JobOffer:
    match = calculate_match(profile, offer)
    offer.compatibility_score = match.score
    offer.match_reason = "\n".join(match.reasons)
    session.commit()
    session.refresh(offer)
    return offer


def clear_offers(session: Session, *, portal: str | None = None) -> dict[str, int | str]:
    offer_ids = [
        offer_id
        for (offer_id,) in session.execute(
            select(JobOffer.id).where(func.lower(JobOffer.portal) == portal.lower()) if portal else select(JobOffer.id)
        )
    ]
    deleted_documents = 0
    deleted_notifications = 0
    deleted_offers = 0
    deleted_hashes = 0

    if offer_ids:
        deleted_documents = session.execute(
            delete(GeneratedDocument).where(GeneratedDocument.job_offer_id.in_(offer_ids))
        ).rowcount or 0
        deleted_notifications = session.execute(
            delete(Notification).where(Notification.job_offer_id.in_(offer_ids))
        ).rowcount or 0
        deleted_offers = session.execute(
            delete(JobOffer).where(JobOffer.id.in_(offer_ids))
        ).rowcount or 0

    if portal:
        deleted_hashes = session.execute(
            delete(JobSeenHash).where(func.lower(JobSeenHash.portal) == portal.lower())
        ).rowcount or 0
    else:
        deleted_hashes = session.execute(delete(JobSeenHash)).rowcount or 0

    session.commit()
    return {
        "portal": portal or "all",
        "offers_deleted": deleted_offers,
        "hashes_deleted": deleted_hashes,
        "notifications_deleted": deleted_notifications,
        "documents_deleted": deleted_documents,
    }


def _merge_offer_fields(
    session: Session,
    offer: JobOffer,
    *,
    company: str,
    portal: str,
    location: str,
    modality: str,
    salary: str,
    description: str,
    requirements: str,
    notes: str,
    published_at: datetime | None,
    found_at: datetime | None,
    raw_posted_text: str,
    normalized_url: str,
    url_hash: str,
    source_id: int | None,
) -> JobOffer:
    offer.company = offer.company or company
    offer.portal = offer.portal or portal
    offer.location = _pick_longer_text(offer.location, location)
    offer.modality = _pick_longer_text(offer.modality, modality)
    offer.salary = _pick_longer_text(offer.salary, salary)
    offer.description = _pick_longer_text(offer.description, description)
    offer.requirements = _pick_longer_text(offer.requirements, requirements)
    offer.notes = _merge_notes(offer.notes, notes)
    offer.raw_posted_text = _pick_longer_text(offer.raw_posted_text, raw_posted_text)
    offer.normalized_url = offer.normalized_url or normalized_url or offer.url
    offer.url_hash = offer.url_hash or url_hash
    offer.source_id = offer.source_id or source_id
    if offer.published_at is None and published_at is not None:
        offer.published_at = published_at
    if offer.found_at is None and found_at is not None:
        offer.found_at = found_at
    session.commit()
    session.refresh(offer)
    return offer


def _pick_longer_text(primary: str, secondary: str) -> str:
    if not primary:
        return secondary
    if not secondary:
        return primary
    return primary if len(primary) >= len(secondary) else secondary


def _merge_notes(primary: str, secondary: str) -> str:
    if not primary:
        return secondary
    if not secondary:
        return primary
    if secondary in primary:
        return primary
    return f"{primary}\n{secondary}".strip()
