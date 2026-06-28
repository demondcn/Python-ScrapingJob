from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from datetime import UTC, datetime

import requests

from .application_types import LINKEDIN_EASY_APPLY, LINKEDIN_EASY_APPLY_LABEL
from .date_utils import format_datetime_for_message, get_detection_display, get_publication_display
from .models import JobOffer, Notification
from .settings import Settings

logger = logging.getLogger(__name__)

TARGET_TYPE_LABELS = {
    "soporte_ti_junior": "Soporte TI / Hardware",
    "hardware_support_junior": "Soporte TI / Hardware",
}


@dataclass(slots=True)
class TelegramDeliveryResult:
    total_chat_ids: int
    delivered_chat_ids: list[str]
    failed_chat_errors: dict[str, str]


class TelegramDeliveryError(RuntimeError):
    def __init__(self, result: TelegramDeliveryResult) -> None:
        self.result = result
        details = "; ".join(
            f"{chat_id}: {error}"
            for chat_id, error in result.failed_chat_errors.items()
        )
        message = "fallo en todos los chats, se deja pendiente"
        if details:
            message = f"{message}: {details}"
        super().__init__(message)


def format_job_alert(
    offer: JobOffer,
    *,
    target_role: str | None = None,
    timezone_name: str = "UTC",
    recipient_labels: list[str] | None = None,
    failed_recipient_labels: list[str] | None = None,
) -> str:
    suggested_target = _normalize_target_role_name(target_role or infer_target_role(offer))
    location_line = " / ".join(part for part in [offer.location, offer.modality] if part)
    try:
        publication_line = get_publication_display(offer, timezone_name)
    except Exception:  # pragma: no cover
        publication_line = "No disponible"
    try:
        detection_line = get_detection_display(offer, timezone_name)
    except Exception:  # pragma: no cover
        detection_line = format_datetime_for_message(datetime.now(UTC), "UTC")

    lines = [
        "Nueva oferta fresca recomendada",
        "",
        f"Cargo: {offer.title}",
        f"Empresa: {offer.company or 'No detectada'}",
        f"Portal: {offer.portal or 'No detectado'}",
        f"Ubicación: {location_line or 'No detectada'}",
        f"Publicada: {publication_line}",
        f"Detectada por JobOps: {detection_line}",
        f"Compatibilidad: {int(offer.compatibility_score)}%",
        "",
        "Motivo:",
    ]
    target_lines = _build_target_lines(suggested_target)
    if target_lines:
        lines[5:5] = target_lines
    delivery_lines = _build_delivery_lines_from_labels(
        recipient_labels or [],
        failed_recipient_labels or [],
    )
    if delivery_lines:
        lines[5 + len(target_lines):5 + len(target_lines)] = delivery_lines
    application_type_lines = _build_application_type_lines(offer)
    if application_type_lines:
        lines[5:5] = application_type_lines
    lines.extend(_build_reason_lines(offer))
    lines.extend(["", "Link para aplicar:", offer.url])
    if suggested_target and offer.id is not None:
        lines.extend(
            [
                "",
                "CV sugerido:",
                f"python main.py resume generate-ats --target {suggested_target} --job-id {offer.id}",
                "",
                "Marcar aplicada:",
                f"python main.py offer update-status --id {offer.id} --status applied",
            ]
        )
    return "\n".join(lines)


def send_job_alert(settings: Settings, offer: JobOffer, *, target_role: str | None = None) -> tuple[bool, str]:
    effective_target_role = _normalize_target_role_name(target_role or infer_target_role(offer))
    chat_ids = _get_telegram_chat_ids(settings, target_role=effective_target_role)
    if not settings.telegram_bot_token or not chat_ids:
        return False, "Credenciales de Telegram incompletas; no se envio notificacion."
    try:
        _post_telegram_message(
            settings,
            format_job_alert(
                offer,
                target_role=effective_target_role,
                timezone_name=settings.timezone_name,
                recipient_labels=_format_chat_labels(settings, chat_ids),
            ),
            target_role=effective_target_role,
        )
    except Exception as exc:
        return False, f"Error enviando Telegram: {exc}"
    return True, "Alerta enviada por Telegram."


def send_job_alert_digest(
    jobs: list[JobOffer],
    settings: Settings,
    *,
    title: str | None = None,
) -> tuple[bool, str, list[JobOffer]]:
    if not jobs:
        return False, "No hay ofertas para enviar en el digest.", []
    if not settings.telegram_bot_token or not _get_all_telegram_chat_ids(settings):
        return False, "Credenciales de Telegram incompletas; no se envio notificacion.", []

    limited_jobs, additional_count = _limit_digest_jobs(jobs, settings.telegram_digest_max_jobs)
    if _has_telegram_chat_targets(settings):
        return _send_targeted_job_alert_digest(
            limited_jobs,
            settings,
            title=title,
            additional_count=additional_count,
        )

    digest_parts = _build_digest_parts(
        limited_jobs,
        settings,
        title=title,
        additional_count=additional_count,
    )
    delivered_jobs: list[JobOffer] = []
    try:
        for message, part_jobs in digest_parts:
            _post_telegram_message(settings, message)
            delivered_jobs.extend(part_jobs)
    except Exception as exc:
        return False, f"Error enviando digest por Telegram: {exc}", delivered_jobs

    message_count = len(digest_parts)
    message = f"digest enviado con {len(delivered_jobs)} ofertas"
    if message_count > 1:
        message = f"{message} en {message_count} mensajes"
    return True, message, delivered_jobs


def _send_targeted_job_alert_digest(
    jobs: list[JobOffer],
    settings: Settings,
    *,
    title: str | None,
    additional_count: int,
) -> tuple[bool, str, list[JobOffer]]:
    chat_jobs: dict[str, list[JobOffer]] = {}
    offer_recipient_chat_ids: dict[int, list[str]] = {}
    for offer in jobs:
        target_role = _get_offer_target_role(offer)
        recipient_chat_ids = _get_telegram_chat_ids(settings, target_role=target_role)
        offer_recipient_chat_ids[_offer_delivery_key(offer)] = recipient_chat_ids
        for chat_id in recipient_chat_ids:
            chat_jobs.setdefault(chat_id, []).append(offer)

    if not chat_jobs:
        logger.error("[telegram] fallo en todos los chats, se deja pendiente")
        return False, "Error enviando digest por Telegram: fallo en todos los chats, se deja pendiente", []

    delivered_keys: set[int] = set()
    failed_chat_errors: dict[str, str] = {}
    snapshots = _apply_offer_recipient_context(jobs, offer_recipient_chat_ids)
    try:
        chat_order = _order_chat_ids_for_delivery(settings, list(chat_jobs))
        for chat_id in chat_order:
            chat_offers = chat_jobs[chat_id]
            _apply_offer_failed_context(chat_offers, offer_recipient_chat_ids, failed_chat_errors)
            digest_parts = _build_digest_parts(
                chat_offers,
                settings,
                title=title,
                additional_count=additional_count,
            )
            for message, part_jobs in digest_parts:
                try:
                    _post_telegram_message(settings, message, chat_ids=[chat_id])
                except Exception as exc:
                    failed_chat_errors[chat_id] = _format_telegram_error(exc, settings)
                    continue
                delivered_keys.update(_offer_delivery_key(offer) for offer in part_jobs)
    finally:
        _restore_offer_delivery_context(snapshots)

    delivered_jobs = [
        offer
        for offer in jobs
        if _offer_delivery_key(offer) in delivered_keys
    ]
    if not delivered_jobs:
        logger.error("[telegram] fallo en todos los chats, se deja pendiente")
        details = "; ".join(
            f"{_format_chat_destination(settings, chat_id)}: {error}"
            for chat_id, error in failed_chat_errors.items()
        )
        message = "Error enviando digest por Telegram: fallo en todos los chats, se deja pendiente"
        if details:
            message = f"{message}: {details}"
        return False, message, []

    message = f"digest enviado con {len(delivered_jobs)} ofertas"
    if failed_chat_errors or len(delivered_jobs) < len(jobs):
        message = f"{message} con errores"
    return True, message, delivered_jobs


def _apply_offer_recipient_context(
    offers: list[JobOffer],
    offer_recipient_chat_ids: dict[int, list[str]],
) -> list[tuple[JobOffer, str, bool, object]]:
    snapshots: list[tuple[JobOffer, str, bool, object]] = []
    for offer in offers:
        snapshots.extend(_set_temporary_offer_attr(
            offer,
            "_jobops_recipient_chat_ids",
            offer_recipient_chat_ids.get(_offer_delivery_key(offer), []),
        ))
        snapshots.extend(_set_temporary_offer_attr(offer, "_jobops_failed_chat_ids", []))
    return snapshots


def _apply_offer_failed_context(
    offers: list[JobOffer],
    offer_recipient_chat_ids: dict[int, list[str]],
    failed_chat_errors: dict[str, str],
) -> None:
    for offer in offers:
        recipient_chat_ids = offer_recipient_chat_ids.get(_offer_delivery_key(offer), [])
        failed_chat_ids = [
            chat_id
            for chat_id in failed_chat_errors
            if chat_id in recipient_chat_ids
        ]
        setattr(offer, "_jobops_failed_chat_ids", failed_chat_ids)


def _restore_offer_delivery_context(snapshots: list[tuple[JobOffer, str, bool, object]]) -> None:
    for offer, attribute, had_previous, previous in reversed(snapshots):
        if had_previous:
            setattr(offer, attribute, previous)
        else:
            try:
                delattr(offer, attribute)
            except AttributeError:
                pass


def _set_temporary_offer_attr(
    offer: JobOffer,
    attribute: str,
    value,
) -> list[tuple[JobOffer, str, bool, object]]:
    had_previous = hasattr(offer, attribute)
    previous = getattr(offer, attribute, None)
    setattr(offer, attribute, value)
    return [(offer, attribute, had_previous, previous)]


def register_notification(session, offer: JobOffer, channel: str, status: str, message: str) -> Notification:
    record = Notification(job_offer_id=offer.id, channel=channel, status=status, message=message)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def infer_target_role(offer: JobOffer) -> str:
    text = _normalize(
        " ".join(
            [
                offer.title or "",
                offer.description or "",
                offer.requirements or "",
                offer.portal or "",
                offer.location or "",
                offer.modality or "",
            ]
        )
    )
    heuristics = [
        ("fullstack_junior", ("full stack", "fullstack", "full-stack", "front y back", "frontend y backend", "e-commerce")),
        ("hardware_support_junior", ("hardware", "mantenimiento de equipos", "reparacion de computadores", "reparacion de computadoras", "impresoras", "cableado", "configuracion de equipos")),
        ("soporte_ti_junior", ("soporte ti", "soporte tecnico", "soporte en sitio", "mesa de ayuda", "help desk", "service desk", "tecnico de sistemas", "auxiliar de sistemas", "nivel 1", "level 1", "n1", "tecnico junior")),
        ("devops_trainee", ("devops", "docker", "linux", "ci/cd", "cloud", "despliegue", "vercel", "neon")),
        ("cloud_support", ("aws", "azure", "cloud support", "monitoreo", "logs")),
        ("qa_junior", ("qa", "pruebas", "testing", "casos de prueba", "bugs")),
        ("backend_junior", ("backend", "api", "python", "java", "node", "express", "nestjs", ".net", "spring boot")),
        ("frontend_junior", ("frontend", "front-end", "react", "next.js", "typescript", "javascript", "tailwind", "angular", "vue")),
        ("soporte_aplicaciones", ("soporte", "tickets", "incidencias", "aplicaciones", "sql", "usuarios")),
        ("infraestructura_junior", ("infraestructura", "mantenimiento", "hardware", "redes", "equipos de computo", "soporte tecnico")),
    ]
    for target, keywords in heuristics:
        if any(keyword in text for keyword in keywords):
            return target
    return "soporte_aplicaciones"


def _build_reason_lines(offer: JobOffer) -> list[str]:
    match_lines = [line.strip() for line in offer.match_reason.splitlines() if line.strip()]
    if not match_lines:
        return ["- Revisar manualmente."]
    return [f"- {line}" for line in match_lines]


def _build_digest_messages(
    jobs: list[JobOffer],
    settings: Settings,
    *,
    title: str | None,
    additional_count: int,
) -> list[str]:
    return [
        message
        for message, _part_jobs in _build_digest_parts(
            jobs,
            settings,
            title=title,
            additional_count=additional_count,
        )
    ]


def _build_digest_parts(
    jobs: list[JobOffer],
    settings: Settings,
    *,
    title: str | None,
    additional_count: int,
) -> list[tuple[str, list[JobOffer]]]:
    sorted_jobs = _sort_digest_jobs(jobs)
    header = _build_digest_header(sorted_jobs, settings, title=title)
    footer = _build_digest_footer(additional_count)
    entries = [
        (_format_digest_entry(index, offer, settings), offer)
        for index, offer in enumerate(sorted_jobs, start=1)
    ]

    max_chars = max(500, settings.telegram_max_message_chars)
    parts: list[tuple[str, list[JobOffer]]] = []
    current_entries: list[str] = []
    current_jobs: list[JobOffer] = []
    for entry, offer in entries:
        candidate_body = "\n\n".join(current_entries + [entry])
        candidate_message = "\n\n".join(part for part in [header, candidate_body, footer] if part)
        if current_entries and len(candidate_message) > max_chars:
            parts.append((
                "\n\n".join(part for part in [header, "\n\n".join(current_entries)] if part),
                current_jobs,
            ))
            current_entries = [entry]
            current_jobs = [offer]
        else:
            current_entries.append(entry)
            current_jobs.append(offer)
    if current_entries:
        parts.append((
            "\n\n".join(part for part in [header, "\n\n".join(current_entries), footer] if part),
            current_jobs,
        ))

    if len(parts) <= 1:
        return parts
    total = len(parts)
    return [
        (f"{message}\n\nParte {index}/{total}", part_jobs)
        for index, (message, part_jobs) in enumerate(parts, start=1)
    ]


def _build_digest_header(jobs: list[JobOffer], settings: Settings, *, title: str | None) -> str:
    return "\n".join(
        [
            title or "🚀 JobOps - Ofertas nuevas recomendadas",
            "",
            f"Encontradas en este ciclo: {len(jobs)}",
            f"Umbral mínimo: {int(settings.match_threshold)}%",
        ]
    )


def _build_digest_footer(additional_count: int) -> str:
    if additional_count <= 0:
        return ""
    return (
        f"Hay {additional_count} ofertas adicionales guardadas. "
        "Revisa con: python main.py offer pending-alerts"
    )


def _format_digest_entry(index: int, offer: JobOffer, settings: Settings) -> str:
    suggested_target = _get_offer_target_role(offer)
    location_line = " / ".join(part for part in [offer.location, offer.modality] if part)
    publication_line = get_publication_display(offer, settings.timezone_name)
    detection_line = get_detection_display(offer, settings.timezone_name)
    lines = [
        f"{index}. {offer.title}",
        f"Empresa: {offer.company or 'No detectada'}",
        f"Portal: {offer.portal or 'No detectado'}",
        f"Ubicación: {location_line or 'No detectada'}",
        f"Compatibilidad: {int(offer.compatibility_score)}%",
        f"Publicada: {publication_line}",
        f"Detectada por JobOps: {detection_line}",
        f"Link: {offer.url}",
    ]
    target_lines = _build_target_lines(suggested_target)
    if target_lines:
        lines[3:3] = target_lines
    delivery_lines = _build_offer_delivery_lines(offer, settings, suggested_target)
    if delivery_lines:
        lines[3 + len(target_lines):3 + len(target_lines)] = delivery_lines
    application_type_lines = _build_application_type_lines(offer)
    if application_type_lines:
        lines[3:3] = application_type_lines
    if offer.id is not None:
        lines.extend(
            [
                "",
                "CV:",
                f"python main.py resume generate-ats --target {suggested_target} --job-id {offer.id}",
                "",
                "Aplicada:",
                f"python main.py offer update-status --id {offer.id} --status applied",
            ]
        )
    return "\n".join(lines)


def _limit_digest_jobs(jobs: list[JobOffer], max_jobs: int) -> tuple[list[JobOffer], int]:
    ordered = _sort_digest_jobs(jobs)
    if max_jobs <= 0:
        return ordered, 0
    limited = ordered[:max_jobs]
    return limited, max(0, len(ordered) - len(limited))


def _build_application_type_lines(offer: JobOffer) -> list[str]:
    if offer.application_type == LINKEDIN_EASY_APPLY:
        return [f"Tipo de solicitud: {LINKEDIN_EASY_APPLY_LABEL}"]
    return []


def _build_target_lines(target_role: str) -> list[str]:
    normalized_target = _normalize_target_role_name(target_role)
    if not normalized_target:
        return []
    lines = [f"📌 Target: {normalized_target}"]
    target_type = TARGET_TYPE_LABELS.get(normalized_target)
    if target_type:
        lines.append(f"Tipo: {target_type}")
    return lines


def _build_offer_delivery_lines(offer: JobOffer, settings: Settings, target_role: str) -> list[str]:
    recipient_chat_ids = getattr(offer, "_jobops_recipient_chat_ids", None)
    if recipient_chat_ids is None:
        recipient_chat_ids = _get_telegram_chat_ids(settings, target_role=target_role)
    failed_chat_ids = getattr(offer, "_jobops_failed_chat_ids", [])
    return _build_delivery_lines_from_labels(
        _format_chat_labels(settings, recipient_chat_ids),
        _format_chat_labels(settings, failed_chat_ids),
    )


def _build_delivery_lines_from_labels(
    recipient_labels: list[str],
    failed_recipient_labels: list[str],
) -> list[str]:
    lines: list[str] = []
    if recipient_labels:
        lines.append(f"📨 Enviado a: {', '.join(recipient_labels)}")
    if failed_recipient_labels:
        lines.append(f"⚠️ Error enviando a: {', '.join(failed_recipient_labels)}")
    return lines


def _get_offer_target_role(offer: JobOffer) -> str:
    target_role = getattr(offer, "_jobops_target_role", "")
    if target_role:
        return _normalize_target_role_name(str(target_role))
    return _normalize_target_role_name(infer_target_role(offer))


def _offer_delivery_key(offer: JobOffer) -> int:
    return offer.id if offer.id is not None else id(offer)


def _sort_digest_jobs(jobs: list[JobOffer]) -> list[JobOffer]:
    return sorted(
        jobs,
        key=lambda item: (
            float(item.compatibility_score or 0),
            ensure_datetime(item.found_at or item.created_at),
        ),
        reverse=True,
    )


def _post_telegram_message(
    settings: Settings,
    message: str,
    *,
    target_role: str | None = None,
    chat_ids: list[str] | None = None,
) -> TelegramDeliveryResult:
    chat_ids = chat_ids if chat_ids is not None else _get_telegram_chat_ids(settings, target_role=target_role)
    delivered_chat_ids: list[str] = []
    failed_chat_errors: dict[str, str] = {}

    for chat_id in chat_ids:
        destination = _format_chat_destination(settings, chat_id)
        try:
            _post_telegram_message_to_chat(settings, message, chat_id)
        except Exception as exc:
            error = _format_telegram_error(exc, settings)
            failed_chat_errors[chat_id] = error
            logger.error("[telegram] error enviando a %s: %s", destination, error)
            continue
        delivered_chat_ids.append(chat_id)
        logger.info("[telegram] enviado a %s", destination)

    result = TelegramDeliveryResult(
        total_chat_ids=len(chat_ids),
        delivered_chat_ids=delivered_chat_ids,
        failed_chat_errors=failed_chat_errors,
    )
    _log_telegram_delivery_summary(result)
    if not delivered_chat_ids:
        raise TelegramDeliveryError(result)
    return result


def _post_telegram_message_to_chat(settings: Settings, message: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": message,
        },
        timeout=15,
    )
    response.raise_for_status()


def _format_chat_labels(settings: Settings, chat_ids: list[str]) -> list[str]:
    return [_format_chat_label(settings, chat_id) for chat_id in chat_ids]


def _format_chat_label(settings: Settings, chat_id: str) -> str:
    chat_labels = getattr(settings, "telegram_chat_labels", {}) or {}
    if chat_labels:
        return chat_labels.get(chat_id, chat_id)
    return chat_id


def _format_chat_destination(settings: Settings, chat_id: str) -> str:
    chat_labels = getattr(settings, "telegram_chat_labels", {}) or {}
    if chat_labels:
        return chat_labels.get(chat_id, chat_id)
    return f"chat_id={chat_id}"


def _order_chat_ids_for_delivery(settings: Settings, chat_ids: list[str]) -> list[str]:
    primary_chat_id = str(getattr(settings, "telegram_chat_id", "")).strip()
    if not primary_chat_id:
        return chat_ids
    secondary_chat_ids = [chat_id for chat_id in chat_ids if chat_id != primary_chat_id]
    primary_chat_ids = [chat_id for chat_id in chat_ids if chat_id == primary_chat_id]
    return secondary_chat_ids + primary_chat_ids


def _get_telegram_chat_ids(settings: Settings, *, target_role: str | None = None) -> list[str]:
    chat_targets = getattr(settings, "telegram_chat_targets", {}) or {}
    if chat_targets:
        normalized_target = _normalize_target_role_name(target_role or "")
        return [
            str(chat_id).strip()
            for chat_id, targets in chat_targets.items()
            if str(chat_id).strip()
            and (_chat_targets_all(targets) or (normalized_target and normalized_target in _normalize_chat_targets(targets)))
        ]

    configured_chat_ids = [
        str(chat_id).strip()
        for chat_id in getattr(settings, "telegram_chat_ids", [])
        if str(chat_id).strip()
    ]
    if configured_chat_ids:
        return configured_chat_ids

    chat_id = str(getattr(settings, "telegram_chat_id", "")).strip()
    return [chat_id] if chat_id else []


def _get_all_telegram_chat_ids(settings: Settings) -> list[str]:
    chat_targets = getattr(settings, "telegram_chat_targets", {}) or {}
    if chat_targets:
        return [
            str(chat_id).strip()
            for chat_id in chat_targets
            if str(chat_id).strip()
        ]
    return _get_telegram_chat_ids(settings)


def _has_telegram_chat_targets(settings: Settings) -> bool:
    return bool(getattr(settings, "telegram_chat_targets", {}) or {})


def _normalize_chat_targets(targets) -> set[str]:
    return {
        _normalize_target_role_name(str(target))
        for target in targets
        if str(target).strip()
    }


def _chat_targets_all(targets) -> bool:
    return "*" in _normalize_chat_targets(targets)


def _log_telegram_delivery_summary(result: TelegramDeliveryResult) -> None:
    delivered_count = len(result.delivered_chat_ids)
    total_count = result.total_chat_ids
    if total_count > 0 and delivered_count == total_count:
        logger.info("[telegram] enviado a %s/%s chats", delivered_count, total_count)
    elif delivered_count > 0:
        logger.warning("[telegram] enviado a %s/%s chats con errores", delivered_count, total_count)
    else:
        logger.error("[telegram] fallo en todos los chats, se deja pendiente")


def _format_telegram_error(exc: Exception, settings: Settings) -> str:
    message = str(exc)
    token = getattr(settings, "telegram_bot_token", "")
    if token:
        message = message.replace(token, "<redacted>")
    return message


def ensure_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _normalize_target_role_name(value: str | None) -> str:
    return re.sub(r"\s+", "_", (value or "").strip().casefold())
