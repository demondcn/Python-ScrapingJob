from __future__ import annotations

from .models import CandidateProfile, JobOffer


ROLE_TEMPLATES: dict[str, str] = {
    "soporte": "Me interesa aportar en soporte a usuarios, seguimiento de incidencias, documentacion y mejora operativa.",
    "devops": "Me interesa aportar mientras sigo fortaleciendo mis habilidades en automatizacion, Linux, cloud y practicas DevOps.",
    "infraestructura": "Me interesa apoyar tareas de infraestructura, soporte operativo y estandarizacion tecnica.",
    "cloud": "Me interesa crecer en entornos cloud y soporte tecnico orientado a continuidad y operacion.",
    "qa": "Me interesa apoyar procesos de calidad, validacion y mejora continua con enfoque ordenado y tecnico.",
    "backend": "Me interesa aportar en desarrollo backend y consolidar mi base tecnica en APIs, datos y buenas practicas.",
}


def _normalize_sentence(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    return text.rstrip(".") + "."


def generate_application_message(profile: CandidateProfile, offer: JobOffer) -> str:
    offer_text = f"{offer.title} {offer.description} {offer.requirements}".lower()
    focus = ROLE_TEMPLATES["backend"]
    for key, value in ROLE_TEMPLATES.items():
        if key in offer_text:
            focus = value
            break

    intro = f"Hola, me interesa la vacante de {offer.title}."
    profile_line = (
        f" Soy {profile.full_name}, {profile.education}, con conocimientos en {profile.skills}."
    )
    summary_text = _normalize_sentence(profile.summary)
    summary_line = f" {summary_text}" if summary_text else ""
    close = f" {focus}"
    return " ".join(part.strip() for part in [intro, profile_line, summary_line, close] if part).strip()
