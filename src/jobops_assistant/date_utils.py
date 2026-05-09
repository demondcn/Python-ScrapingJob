from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone, tzinfo
import logging
import re
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import JobOffer


logger = logging.getLogger(__name__)
DEFAULT_TIMEZONE = "America/Bogota"


def get_configured_timezone(timezone_name: str | None) -> tzinfo:
    configured_name = (timezone_name or "").strip() or DEFAULT_TIMEZONE
    if configured_name.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(configured_name)
    except ZoneInfoNotFoundError:
        logger.warning("No time zone found with key %s. Falling back to UTC.", configured_name)
        return timezone.utc
    except Exception as exc:  # pragma: no cover
        logger.warning("Error resolving timezone %s: %s. Falling back to UTC.", configured_name, exc)
        return timezone.utc


def ensure_utc_datetime(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_relative_posted_text(text: str, now: datetime | None = None) -> datetime | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None

    current_time = ensure_utc_datetime(now) or datetime.now(UTC)
    if any(token in normalized for token in ("hoy", "today", "publicada hoy", "publicado hoy", "just posted", "new")):
        return current_time
    if "ayer" in normalized or "yesterday" in normalized:
        return current_time - timedelta(days=1)

    patterns = (
        (r"(?:hace|ago)\s+(\d+)\s+(?:hora|horas|hour|hours)", "hours"),
        (r"(?:hace|ago)\s+(\d+)\s+(?:dia|dias|day|days)", "days"),
        (r"(?:hace|ago)\s+(\d+)\s+(?:minuto|minutos|minute|minutes)", "minutes"),
    )
    for pattern, unit in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        value = int(match.group(1))
        return current_time - timedelta(**{unit: value})
    return None


def format_datetime_for_message(dt: datetime | None, timezone_name: str = DEFAULT_TIMEZONE) -> str:
    normalized = ensure_utc_datetime(dt)
    if normalized is None:
        return "No disponible"
    try:
        localized = normalized.astimezone(get_configured_timezone(timezone_name))
    except Exception as exc:  # pragma: no cover
        logger.warning("Error formatting datetime for timezone %s: %s. Falling back to UTC.", timezone_name, exc)
        localized = normalized.astimezone(timezone.utc)
    if localized.hour == 0 and localized.minute == 0 and localized.second == 0:
        return localized.strftime("%Y-%m-%d")
    return localized.strftime("%Y-%m-%d %H:%M")


def get_publication_display(offer: JobOffer, timezone_name: str = DEFAULT_TIMEZONE) -> str:
    try:
        if offer.published_at is not None:
            return format_datetime_for_message(offer.published_at, timezone_name)
        if (offer.raw_posted_text or "").strip():
            return offer.raw_posted_text.strip()
        return "No disponible"
    except Exception as exc:  # pragma: no cover
        logger.warning("Error resolving publication display: %s", exc)
        return "No disponible"


def get_detection_display(
    offer: JobOffer,
    timezone_name: str = DEFAULT_TIMEZONE,
    *,
    now: datetime | None = None,
) -> str:
    try:
        detection_time = ensure_utc_datetime(offer.found_at) or ensure_utc_datetime(now) or datetime.now(UTC)
        return format_datetime_for_message(detection_time, timezone_name)
    except Exception as exc:  # pragma: no cover
        logger.warning("Error resolving detection display: %s", exc)
        return format_datetime_for_message(ensure_utc_datetime(now) or datetime.now(UTC), "UTC")


def _normalize_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip().casefold()
    normalized = unicodedata.normalize("NFKD", cleaned)
    return "".join(char for char in normalized if not unicodedata.combining(char))
