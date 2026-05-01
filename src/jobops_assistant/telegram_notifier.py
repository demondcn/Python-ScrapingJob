from __future__ import annotations

import re

import requests

from .models import JobOffer, Notification
from .settings import Settings


def format_job_alert(offer: JobOffer, *, target_role: str | None = None) -> str:
    suggested_target = target_role or infer_target_role(offer)
    match_lines = [line.strip() for line in offer.match_reason.splitlines() if line.strip()]
    location_line = " / ".join(part for part in [offer.location, offer.modality] if part)

    lines = [
        "Nueva oferta fresca recomendada",
        "",
        f"Cargo: {offer.title}",
        f"Empresa: {offer.company or 'No detectada'}",
        f"Portal: {offer.portal or 'No detectado'}",
        f"Ubicación: {location_line or 'No detectada'}",
        f"Compatibilidad: {int(offer.compatibility_score)}%",
        "",
        "Motivo:",
    ]
    if match_lines:
        lines.extend(f"- {line}" for line in match_lines)
    else:
        lines.append("- Revisar manualmente.")

    lines.extend(
        [
            "",
            "Link para aplicar:",
            offer.url,
        ]
    )

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

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": settings.telegram_chat_id,
            "text": format_job_alert(offer, target_role=target_role),
        },
        timeout=15,
    )
    response.raise_for_status()
    return True, "Alerta enviada por Telegram."


def register_notification(session, offer: JobOffer, channel: str, status: str, message: str) -> Notification:
    record = Notification(job_offer_id=offer.id, channel=channel, status=status, message=message)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def infer_target_role(offer: JobOffer) -> str:
    text = _normalize(" ".join([offer.title, offer.description, offer.requirements, offer.portal, offer.location, offer.modality]))
    heuristics = [
        ("soporte_aplicaciones", ("soporte", "tickets", "incidencias", "aplicaciones", "sql", "usuarios")),
        ("infraestructura_junior", ("infraestructura", "mantenimiento", "hardware", "redes", "equipos de computo", "soporte tecnico")),
        ("devops_trainee", ("devops", "docker", "linux", "ci/cd", "cloud", "despliegue", "vercel", "neon")),
        ("cloud_support", ("aws", "azure", "cloud support", "monitoreo", "logs")),
        ("qa_junior", ("qa", "pruebas", "testing", "casos de prueba", "bugs")),
        ("backend_junior", ("backend", "api", "python", "java", "node", "express")),
        ("fullstack_junior", ("react", "next.js", "typescript", "frontend", "full stack", "fullstack")),
    ]
    for target, keywords in heuristics:
        if any(keyword in text for keyword in keywords):
            return target
    return "soporte_aplicaciones"


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()
