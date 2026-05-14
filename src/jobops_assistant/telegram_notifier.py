from __future__ import annotations

import re
from datetime import UTC, datetime

import requests

from .date_utils import format_datetime_for_message, get_detection_display, get_publication_display
from .models import JobOffer, Notification
from .settings import Settings


def format_job_alert(
    offer: JobOffer,
    *,
    target_role: str | None = None,
    timezone_name: str = "UTC",
) -> str:
    suggested_target = target_role or infer_target_role(offer)
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
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False, "Credenciales de Telegram incompletas; no se envio notificacion."
    _post_telegram_message(
        settings,
        format_job_alert(
            offer,
            target_role=target_role,
            timezone_name=settings.timezone_name,
        ),
    )
    return True, "Alerta enviada por Telegram."


def send_job_alert_digest(
    jobs: list[JobOffer],
    settings: Settings,
    *,
    title: str | None = None,
) -> tuple[bool, str, list[JobOffer]]:
    if not jobs:
        return False, "No hay ofertas para enviar en el digest.", []
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False, "Credenciales de Telegram incompletas; no se envio notificacion.", []

    limited_jobs, additional_count = _limit_digest_jobs(jobs, settings.telegram_digest_max_jobs)
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
        ("fullstack_junior", ("full stack", "fullstack", "react", "next.js", "node.js", "express", "postgresql", "e-commerce")),
        ("soporte_aplicaciones", ("soporte", "tickets", "incidencias", "aplicaciones", "sql", "usuarios")),
        ("infraestructura_junior", ("infraestructura", "mantenimiento", "hardware", "redes", "equipos de computo", "soporte tecnico")),
        ("devops_trainee", ("devops", "docker", "linux", "ci/cd", "cloud", "despliegue", "vercel", "neon")),
        ("cloud_support", ("aws", "azure", "cloud support", "monitoreo", "logs")),
        ("qa_junior", ("qa", "pruebas", "testing", "casos de prueba", "bugs")),
        ("backend_junior", ("backend", "api", "python", "java", "node", "express", "nestjs", ".net", "spring boot")),
        ("frontend_junior", ("frontend", "front-end", "react", "next.js", "typescript", "javascript", "tailwind", "angular", "vue")),
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
    suggested_target = infer_target_role(offer)
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


def _sort_digest_jobs(jobs: list[JobOffer]) -> list[JobOffer]:
    return sorted(
        jobs,
        key=lambda item: (
            float(item.compatibility_score or 0),
            ensure_datetime(item.found_at or item.created_at),
        ),
        reverse=True,
    )


def _post_telegram_message(settings: Settings, message: str) -> None:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": settings.telegram_chat_id,
            "text": message,
        },
        timeout=15,
    )
    response.raise_for_status()


def ensure_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()
