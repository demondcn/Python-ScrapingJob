from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .matcher import analyze_relevance_for_target, calculate_match
from .models import DiscardedJob, JobOffer
from .scrapers.base_scraper import ScrapedJob


@dataclass(slots=True)
class DiscardedJobReview:
    job: ScrapedJob
    reasons: list[str]
    detected_keywords: list[str]
    preliminary_score: int | None = None


@dataclass(slots=True)
class DiscardedReprocessResult:
    discarded_job_id: int
    title: str
    accepted: bool
    reasons: list[str]
    offer_id: int | None = None


def build_url_hash(normalized_url: str) -> str:
    return sha256((normalized_url or "").encode("utf-8")).hexdigest()


def analyze_scraped_job_for_discard(
    job: ScrapedJob,
    *,
    target_role: str,
    profile=None,
) -> DiscardedJobReview | None:
    analysis = analyze_relevance_for_target(job, target_role)
    if analysis.relevant:
        return None
    match = calculate_match(profile, build_offer_preview_from_scraped_job(job))
    return DiscardedJobReview(
        job=job,
        reasons=analysis.reasons,
        detected_keywords=analysis.detected_keywords,
        preliminary_score=match.score,
    )


def build_offer_preview_from_scraped_job(job: ScrapedJob, *, source_id: int | None = None) -> JobOffer:
    normalized_url = job.url or ""
    return JobOffer(
        title=job.title or "",
        company=job.company or "",
        portal=job.portal or "",
        location=job.location or "",
        modality=job.modality or "",
        salary=job.salary or "",
        url=normalized_url,
        description=job.description or "",
        requirements=job.requirements or "",
        raw_posted_text=job.raw_posted_text or "",
        published_at=_ensure_utc(job.published_at),
        found_at=_ensure_utc(job.found_at),
        normalized_url=normalized_url,
        url_hash=build_url_hash(normalized_url),
        source_id=source_id if source_id is not None else job.source_id,
    )


def build_scraped_job_from_discarded(record: DiscardedJob) -> ScrapedJob:
    return ScrapedJob(
        title=record.title or "",
        company=record.company or "",
        portal=record.portal or "",
        location=record.location or "",
        modality=record.modality or "",
        salary=record.salary or "",
        url=record.normalized_url or record.url or "",
        description=record.description or "",
        requirements=record.requirements or "",
        published_at=None,
        found_at=_ensure_utc(record.found_at) or datetime.now(UTC),
        raw_posted_text=record.raw_posted_text or "",
        source_id=record.source_id,
    )


def upsert_discarded_job(
    session: Session,
    *,
    portal: str,
    source_id: int | None,
    target_role: str,
    source_url: str,
    review: DiscardedJobReview,
) -> DiscardedJob:
    job = review.job
    normalized_url = job.url or ""
    url_hash = build_url_hash(normalized_url)
    seen_at = _ensure_utc(job.found_at) or datetime.now(UTC)
    reasons_json = serialize_text_list(review.reasons)
    keywords_json = serialize_text_list(review.detected_keywords)
    existing = get_discarded_job_by_url_hash(session, url_hash)
    if existing is not None:
        existing.portal = portal or existing.portal
        existing.source_id = source_id if source_id is not None else existing.source_id
        existing.target_role = target_role or existing.target_role
        existing.title = _pick_longer_text(existing.title, job.title)
        existing.company = _pick_longer_text(existing.company, job.company)
        existing.location = _pick_longer_text(existing.location, job.location)
        existing.modality = _pick_longer_text(existing.modality, job.modality)
        existing.salary = _pick_longer_text(existing.salary, job.salary)
        existing.url = existing.url or normalized_url
        existing.description = _pick_longer_text(existing.description, job.description)
        existing.requirements = _pick_longer_text(existing.requirements, job.requirements)
        existing.raw_posted_text = _pick_longer_text(existing.raw_posted_text, job.raw_posted_text)
        existing.compatibility_score = (
            float(review.preliminary_score)
            if review.preliminary_score is not None
            else existing.compatibility_score
        )
        existing.discard_reasons = reasons_json
        existing.detected_keywords = keywords_json
        existing.source_url = source_url or existing.source_url
        if existing.found_at is None and job.found_at is not None:
            existing.found_at = _ensure_utc(job.found_at)
        existing.normalized_url = existing.normalized_url or normalized_url
        existing.seen_count = max(1, existing.seen_count) + 1
        existing.last_seen_at = seen_at
        session.commit()
        session.refresh(existing)
        return existing

    discarded = DiscardedJob(
        portal=portal or job.portal or "",
        source_id=source_id,
        target_role=target_role or "",
        title=job.title or "",
        company=job.company or "",
        location=job.location or "",
        modality=job.modality or "",
        salary=job.salary or "",
        url=normalized_url,
        description=job.description or "",
        requirements=job.requirements or "",
        raw_posted_text=job.raw_posted_text or "",
        compatibility_score=float(review.preliminary_score) if review.preliminary_score is not None else None,
        discard_reasons=reasons_json,
        detected_keywords=keywords_json,
        source_url=source_url or "",
        found_at=_ensure_utc(job.found_at),
        normalized_url=normalized_url,
        url_hash=url_hash,
        seen_count=1,
        last_seen_at=seen_at,
    )
    session.add(discarded)
    session.commit()
    session.refresh(discarded)
    return discarded


def list_discarded_jobs(
    session: Session,
    *,
    portal: str | None = None,
    target_role: str | None = None,
    limit: int | None = 20,
) -> list[DiscardedJob]:
    stmt = select(DiscardedJob)
    if portal:
        stmt = stmt.where(func.lower(DiscardedJob.portal) == portal.lower())
    if target_role:
        stmt = stmt.where(func.lower(DiscardedJob.target_role) == target_role.lower())
    stmt = stmt.order_by(
        func.coalesce(DiscardedJob.last_seen_at, DiscardedJob.found_at, DiscardedJob.created_at).desc(),
        DiscardedJob.id.desc(),
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def count_discarded_jobs(
    session: Session,
    *,
    portal: str | None = None,
    target_role: str | None = None,
) -> int:
    stmt = select(func.count(DiscardedJob.id))
    if portal:
        stmt = stmt.where(func.lower(DiscardedJob.portal) == portal.lower())
    if target_role:
        stmt = stmt.where(func.lower(DiscardedJob.target_role) == target_role.lower())
    return int(session.scalar(stmt) or 0)


def get_discarded_job_by_id(session: Session, discarded_job_id: int) -> DiscardedJob | None:
    return session.get(DiscardedJob, discarded_job_id)


def get_discarded_job_by_url_hash(session: Session, url_hash: str) -> DiscardedJob | None:
    if not url_hash:
        return None
    return session.scalar(select(DiscardedJob).where(DiscardedJob.url_hash == url_hash))


def clear_discarded_jobs(
    session: Session,
    *,
    portal: str | None = None,
    target_role: str | None = None,
) -> int:
    stmt = delete(DiscardedJob)
    if portal:
        stmt = stmt.where(func.lower(DiscardedJob.portal) == portal.lower())
    if target_role:
        stmt = stmt.where(func.lower(DiscardedJob.target_role) == target_role.lower())
    deleted = session.execute(stmt).rowcount or 0
    session.commit()
    return deleted


def remove_discarded_job_by_url_hash(session: Session, url_hash: str) -> int:
    if not url_hash:
        return 0
    deleted = session.execute(delete(DiscardedJob).where(DiscardedJob.url_hash == url_hash)).rowcount or 0
    if deleted:
        session.commit()
    return deleted


def export_discarded_jobs(
    session: Session,
    *,
    file_path: Path,
    portal: str | None = None,
    target_role: str | None = None,
) -> int:
    records = list_discarded_jobs(session, portal=portal, target_role=target_role, limit=None)
    payload = [_build_export_row(record) for record in records]
    file_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = file_path.suffix.lower()
    if suffix == ".json":
        file_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        return len(payload)
    if suffix == ".csv":
        fieldnames = [
            "id",
            "portal",
            "target_role",
            "title",
            "company",
            "location",
            "url",
            "discard_reasons",
            "detected_keywords",
            "found_at",
            "seen_count",
        ]
        with file_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in payload:
                writer.writerow(row)
        return len(payload)
    raise ValueError("Formato de exportacion no soportado. Usa .csv o .json")


def reprocess_discarded_jobs(
    session: Session,
    *,
    create_offer,
    refresh_offer_match,
    profile,
    discarded_job_id: int | None = None,
    portal: str | None = None,
    target_role: str | None = None,
) -> list[DiscardedReprocessResult]:
    stmt = select(DiscardedJob)
    if discarded_job_id is not None:
        stmt = stmt.where(DiscardedJob.id == discarded_job_id)
    if portal:
        stmt = stmt.where(func.lower(DiscardedJob.portal) == portal.lower())
    if target_role:
        stmt = stmt.where(func.lower(DiscardedJob.target_role) == target_role.lower())
    stmt = stmt.order_by(DiscardedJob.id.asc())

    results: list[DiscardedReprocessResult] = []
    for record in list(session.scalars(stmt)):
        scraped_job = build_scraped_job_from_discarded(record)
        review = analyze_scraped_job_for_discard(
            scraped_job,
            target_role=record.target_role,
            profile=profile,
        )
        if review is not None:
            record.discard_reasons = serialize_text_list(review.reasons)
            record.detected_keywords = serialize_text_list(review.detected_keywords)
            if review.preliminary_score is not None:
                record.compatibility_score = float(review.preliminary_score)
            session.commit()
            session.refresh(record)
            results.append(
                DiscardedReprocessResult(
                    discarded_job_id=record.id,
                    title=record.title,
                    accepted=False,
                    reasons=review.reasons,
                )
            )
            continue

        offer = create_offer(
            session,
            title=record.title,
            company=record.company,
            portal=record.portal,
            location=record.location,
            modality=record.modality,
            salary=record.salary,
            url=record.normalized_url or record.url,
            description=record.description,
            requirements=record.requirements,
            found_at=_ensure_utc(record.found_at),
            raw_posted_text=record.raw_posted_text,
            normalized_url=record.normalized_url or record.url,
            url_hash=record.url_hash,
            source_id=record.source_id,
        )
        refresh_offer_match(session, offer, profile)
        record_id = record.id
        session.delete(record)
        session.commit()
        results.append(
            DiscardedReprocessResult(
                discarded_job_id=record_id,
                title=offer.title,
                accepted=True,
                reasons=["aceptada por el matcher actual"],
                offer_id=offer.id,
            )
        )
    return results


def parse_text_list(value: str) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, str):
        cleaned = parsed.strip()
        return [cleaned] if cleaned else []
    return [json.dumps(parsed, ensure_ascii=False)]


def serialize_text_list(values: list[str]) -> str:
    cleaned = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return json.dumps(cleaned, ensure_ascii=False)


def short_discard_reason(record: DiscardedJob) -> str:
    reasons = parse_text_list(record.discard_reasons)
    return reasons[0] if reasons else "sin razon registrada"


def _build_export_row(record: DiscardedJob) -> dict[str, object]:
    return {
        "id": record.id,
        "portal": record.portal,
        "target_role": record.target_role,
        "title": record.title,
        "company": record.company,
        "location": record.location,
        "url": record.normalized_url or record.url,
        "discard_reasons": "; ".join(parse_text_list(record.discard_reasons)),
        "detected_keywords": "; ".join(parse_text_list(record.detected_keywords)),
        "found_at": _json_default(record.found_at),
        "seen_count": record.seen_count,
    }


def _pick_longer_text(primary: str, secondary: str) -> str:
    if not primary:
        return secondary or ""
    if not secondary:
        return primary
    return primary if len(primary) >= len(secondary) else secondary


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _json_default(value):
    if isinstance(value, datetime):
        return _ensure_utc(value).isoformat()
    return value
