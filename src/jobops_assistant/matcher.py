from __future__ import annotations

from .models import CandidateProfile, JobOffer
from .schemas import MatchResult


POSITIVE_RULES: dict[str, tuple[int, str]] = {
    "junior": (18, "Acepta perfil junior"),
    "trainee": (18, "Acepta perfil trainee"),
    "soporte": (14, "Menciona soporte"),
    "sql": (9, "Menciona SQL"),
    "git": (8, "Menciona Git"),
    "linux": (8, "Menciona Linux"),
    "docker": (8, "Menciona Docker"),
    "ci/cd": (8, "Menciona CI/CD"),
    "cloud": (8, "Menciona cloud"),
    "aws": (7, "Menciona AWS"),
    "azure": (7, "Menciona Azure"),
    "aplicaciones": (10, "Relacionada con soporte de aplicaciones"),
    "tickets": (7, "Menciona tickets o incidencias"),
    "remoto": (5, "Modalidad remota"),
    "hibrido": (4, "Modalidad hibrida"),
}

NEGATIVE_RULES: dict[str, tuple[int, str]] = {
    "senior": (-22, "Pide perfil senior"),
    "4 anos": (-18, "Pide experiencia superior a 3 anos"),
    "5 anos": (-20, "Pide experiencia alta"),
    "ingles avanzado": (-14, "Exige ingles avanzado"),
    "kubernetes avanzado": (-14, "Exige Kubernetes avanzado"),
    "terraform avanzado": (-14, "Exige Terraform avanzado"),
}


def calculate_match(profile: CandidateProfile | None, offer: JobOffer) -> MatchResult:
    text = " ".join(
        [
            offer.title or "",
            offer.description or "",
            offer.requirements or "",
            offer.location or "",
            offer.modality or "",
        ]
    ).lower()
    score = 35
    reasons: list[str] = []

    for keyword, (points, reason) in POSITIVE_RULES.items():
        if keyword in text:
            score += points
            reasons.append(reason)

    for keyword, (points, reason) in NEGATIVE_RULES.items():
        if keyword in text:
            score += points
            reasons.append(reason)

    if profile:
        profile_text = " ".join([profile.skills, profile.summary, profile.target_roles]).lower()
        if "devops" in profile_text and any(token in text for token in ("docker", "linux", "cloud", "ci/cd")):
            score += 6
            reasons.append("Se alinea con el objetivo de crecimiento en DevOps")
        if "backend" in profile_text and "python" in text:
            score += 4
            reasons.append("Coincide con interes en backend")

    score = max(0, min(score, 100))
    if not reasons:
        reasons.append("Sin coincidencias claras; revisar manualmente")

    return MatchResult(score=score, reasons=reasons)

