from __future__ import annotations

import re
import unicodedata

from .models import CandidateProfile, JobOffer
from .schemas import MatchResult, RelevanceAnalysis


GENERAL_SENIOR_SIGNALS = (
    "senior",
    "semi senior",
    "semisenior",
    "lider",
    "lider tecnico",
    "lead",
    "arquitecto",
    "architect",
    "coordinador",
    "manager",
    "jefe",
    "+3 anos",
    "3+ anos",
    "minimo 3 anos",
    "mas de 3 anos",
    "4 anos",
    "5 anos",
    "experiencia avanzada",
    "experto",
    "especialista senior",
)

GENERAL_ENTRY_SIGNALS = (
    "junior",
    "trainee",
    "aprendiz",
    "practicante",
    "intern",
    "estudiante en practica",
    "estudiante en practicas",
    "estudiante practica",
    "practica",
    "practicas",
    "tecnico",
    "tecnologo",
    "entry level",
    "recien egresado",
    "0 a 1 ano",
    "1 ano",
    "sin experiencia",
    "desarrollador junior",
    "analista junior",
)

ADMINISTRATIVE_ROLE_SIGNALS = (
    "asesor",
    "recepcionista",
    "logistica",
    "logistico",
    "automotriz",
    "comercial",
    "contable",
    "abogado",
    "contador",
    "promotor",
    "libranza",
    "seguros",
    "nomina",
    "postventas",
    "operativo",
    "administrativo",
    "ventas",
)

FRONTEND_FALSE_POSITIVE_SIGNALS = (
    "front desk",
    "front office",
    "frontoffice",
)

TARGET_TITLE_SIGNALS: dict[str, tuple[str, ...]] = {
    "backend_junior": (
        "backend",
        "back-end",
        "desarrollador backend",
        "python",
        "java",
        "node.js",
        "node",
        "api",
        "apis",
        "sql",
        "postgresql",
        "mysql",
        "bases de datos",
        "base de datos",
        "desarrollo de software",
        "fullstack",
        "full stack",
        "full-stack",
    ),
    "frontend_junior": (
        "frontend",
        "front-end",
        "front end",
        "desarrollador frontend",
        "desarrollador front end",
        "desarrollador web",
        "desarrollo front",
        "flutter",
        "react",
        "angular",
        "vue",
        "html",
        "css",
        "javascript",
        "typescript",
        "ecommerce",
        "e-commerce",
        "fullstack",
        "full stack",
        "front y back",
        "frontend y backend",
    ),
    "fullstack_junior": (
        "fullstack",
        "full stack",
        "full-stack",
        "developer fullstack",
        "desarrollador fullstack",
        "desarrollador full stack",
        "front y back",
        "frontend y backend",
        "desarrollo web",
    ),
    "devops_trainee": (
        "devops",
        "ingeniero devops",
        "devops engineer",
        "junior devops",
    ),
    "soporte_aplicaciones": (
        "soporte de aplicaciones",
        "application support",
        "soporte tecnico",
        "mesa de ayuda",
        "help desk",
    ),
    "infraestructura_junior": (
        "it",
        "sistemas",
        "tecnologia de la informacion",
        "mantenimiento de equipos",
        "soporte ti",
        "auxiliar de sistemas",
        "tecnico de sistemas",
        "area it",
        "it sistemas",
        "infraestructura",
        "hardware",
        "software",
        "mantenimiento",
    ),
    "cloud_support": (
        "cloud",
        "aws",
        "azure",
        "gcp",
        "soporte cloud",
    ),
    "qa_junior": (
        "qa",
        "testing",
        "pruebas",
        "quality assurance",
    ),
}

TARGET_CONTENT_SIGNALS: dict[str, tuple[str, ...]] = {
    "backend_junior": (
        "backend",
        "back-end",
        "node.js",
        "express",
        "nestjs",
        "python",
        "java",
        "spring boot",
        "c#",
        ".net",
        "api",
        "apis",
        "sql",
        "postgresql",
        "mysql",
        "mongodb",
        "bases de datos",
        "base de datos",
        "desarrollo de software",
    ),
    "frontend_junior": (
        "frontend",
        "front-end",
        "front end",
        "flutter",
        "react",
        "angular",
        "vue",
        "html",
        "css",
        "javascript",
        "typescript",
        "ecommerce",
        "e-commerce",
        "desarrollo web",
        "ui",
        "componentes",
    ),
    "fullstack_junior": (
        "fullstack",
        "full stack",
        "full-stack",
        "front y back",
        "frontend y backend",
        "react",
        "javascript",
        "typescript",
        "node.js",
        "python",
        "java",
        "api",
        "apis",
        "sql",
        "postgresql",
        "mysql",
        "desarrollo web",
    ),
    "devops_trainee": (
        "devops",
        "docker",
        "linux",
        "ci/cd",
        "github actions",
        "cloud",
        "aws",
        "azure",
        "gcp",
        "despliegue",
        "automatizacion",
        "pipelines",
        "terraform",
        "kubernetes",
    ),
    "soporte_aplicaciones": (
        "soporte",
        "tickets",
        "incidencias",
        "usuarios",
        "sql",
        "aplicaciones web",
        "application support",
        "mesa de ayuda",
    ),
    "infraestructura_junior": (
        "it",
        "sistemas",
        "carreras sistemas o afines",
        "tecnologia de la informacion",
        "mantenimiento de equipos",
        "soporte ti",
        "auxiliar de sistemas",
        "tecnico de sistemas",
        "area it",
        "it sistemas",
        "infraestructura",
        "hardware",
        "software",
        "configuracion de equipos",
        "mantenimiento",
    ),
    "cloud_support": (
        "cloud",
        "aws",
        "azure",
        "gcp",
        "docker",
        "linux",
        "despliegue",
    ),
    "qa_junior": (
        "qa",
        "testing",
        "pruebas",
        "bugs",
        "casos de prueba",
        "validacion",
        "apis",
    ),
}

FULLSTACK_FRONTEND_SIGNALS = (
    "frontend",
    "front-end",
    "front end",
    "front y back",
    "frontend y backend",
    "react",
    "next.js",
    "javascript",
    "typescript",
    "html",
    "css",
    "flutter",
    "vue",
    "angular",
)

FULLSTACK_BACKEND_SIGNALS = (
    "backend",
    "back-end",
    "node.js",
    "express",
    "python",
    "java",
    "api",
    "apis",
    "sql",
    "postgresql",
    "mysql",
    "mongodb",
)

TARGET_RELEVANCE_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "backend_junior": {
        "strong": (
            "backend",
            "back-end",
            "api",
            "apis",
            "api rest",
            "rest",
            "node.js",
            "express",
            "nestjs",
            "python",
            "java",
            "spring boot",
            "c#",
            ".net",
            "sql",
            "postgresql",
            "mysql",
            "mongodb",
            "logica de negocio",
        ),
        "exclude": ("frontend", "front-end", "ux", "ui", "diseno grafico", "qa", "testing", "pruebas software", "soporte tecnico"),
    },
    "frontend_junior": {
        "strong": (
            "frontend",
            "front-end",
            "react",
            "next.js",
            "angular",
            "vue",
            "html",
            "css",
            "javascript",
            "typescript",
            "tailwind",
            "interfaces",
            "ui",
            "diseno responsivo",
            "responsive",
            "consumo de apis",
        ),
        "exclude": ("devops", "infraestructura", "redes", "soporte ti", "spring boot"),
    },
    "fullstack_junior": {
        "strong": ("fullstack", "full stack", "desarrollador fullstack", "panel administrativo", "dashboards", "e-commerce"),
        "frontend": ("react", "next.js", "frontend", "javascript", "typescript", "html", "css"),
        "backend": ("backend", "node.js", "express", "api", "apis", "sql", "postgresql", "mysql", "mongodb"),
        "exclude": ("ux", "diseno grafico", "solo frontend", "solo backend"),
    },
    "devops_trainee": {
        "strong": (
            "devops",
            "ci/cd",
            "docker",
            "linux",
            "github actions",
            "despliegue",
            "cloud",
            "aws",
            "azure",
            "automatizacion",
            "infraestructura",
            "pipelines",
        ),
        "exclude": ("ux", "diseno grafico", "community manager"),
    },
    "soporte_aplicaciones": {
        "strong": (
            "soporte de aplicaciones",
            "soporte tecnico de software",
            "application support",
            "sql",
            "tickets",
            "incidencias",
            "usuarios",
            "documentacion",
            "mesa de ayuda",
            "aplicaciones web",
            "soporte",
        ),
        "exclude": ("ventas", "diseno grafico", "community manager"),
    },
    "infraestructura_junior": {
        "strong": (
            "infraestructura",
            "soporte ti",
            "auxiliar de sistemas",
            "tecnico de sistemas",
            "redes",
            "equipos de computo",
            "mantenimiento",
            "inventario tecnologico",
            "hardware",
            "software",
            "configuracion de equipos",
        ),
        "exclude": ("frontend", "react", "ux", "diseno grafico"),
    },
    "cloud_support": {
        "strong": ("cloud", "aws", "azure", "gcp", "despliegue", "soporte cloud", "infraestructura", "docker", "linux"),
        "exclude": ("ux", "diseno grafico"),
    },
    "qa_junior": {
        "strong": ("qa", "testing", "pruebas", "casos de prueba", "bugs", "validacion", "apis"),
        "exclude": ("ux", "diseno grafico"),
    },
}


TARGET_RULES: dict[str, dict[str, object]] = {
    "devops_trainee": {
        "keywords": (
            "devops",
            "docker",
            "linux",
            "ci/cd",
            "cloud",
            "aws",
            "azure",
            "despliegue",
            "automatizacion",
            "vercel",
            "neon",
            "postgresql",
            "git",
            "github",
        ),
        "weight": 5,
    },
    "soporte_aplicaciones": {
        "keywords": (
            "soporte",
            "soporte a usuarios",
            "incidencias",
            "tickets",
            "aplicaciones",
            "aplicaciones web",
            "sql",
            "documentacion",
            "reportes",
            "diagnostico",
        ),
        "weight": 5,
    },
    "infraestructura_junior": {
        "keywords": (
            "infraestructura",
            "mantenimiento",
            "hardware",
            "software",
            "redes",
            "equipos de computo",
            "inventario",
            "soporte tecnico",
            "configuracion",
            "linea de comandos",
        ),
        "weight": 5,
    },
    "cloud_support": {
        "keywords": (
            "cloud",
            "aws",
            "azure",
            "monitoreo",
            "logs",
            "soporte",
            "despliegue",
            "vercel",
            "neon",
        ),
        "weight": 5,
    },
    "qa_junior": {
        "keywords": (
            "qa",
            "pruebas",
            "testing",
            "casos de prueba",
            "bugs",
            "validacion",
            "documentacion",
            "reportes",
            "apis",
        ),
        "weight": 5,
    },
    "backend_junior": {
        "keywords": (
            "backend",
            "back-end",
            "desarrollador backend",
            "node.js",
            "node",
            "express",
            "nestjs",
            "python",
            "java",
            "spring boot",
            "c#",
            ".net",
            "apis rest",
            "rest api",
            "json",
            "sql",
            "postgresql",
            "mysql",
            "mongodb",
            "autenticacion",
            "logica de negocio",
            "integracion de apis",
            "git",
            "github",
            "docker",
            "pruebas unitarias",
        ),
        "weight": 5,
    },
    "frontend_junior": {
        "keywords": (
            "frontend",
            "front-end",
            "desarrollador frontend",
            "html",
            "css",
            "javascript",
            "typescript",
            "react",
            "next.js",
            "angular",
            "vue",
            "tailwind",
            "bootstrap",
            "responsive",
            "diseno responsivo",
            "consumo de apis",
            "interfaces",
            "ui",
            "componentes",
            "git",
            "github",
            "vercel",
        ),
        "weight": 5,
    },
    "fullstack_junior": {
        "keywords": (
            "fullstack",
            "full stack",
            "desarrollador fullstack",
            "frontend",
            "backend",
            "react",
            "next.js",
            "node.js",
            "express",
            "typescript",
            "javascript",
            "apis",
            "sql",
            "postgresql",
            "mysql",
            "mongodb",
            "dashboards",
            "e-commerce",
            "panel administrativo",
            "autenticacion",
            "despliegue",
            "vercel",
            "neon",
            "git",
            "github",
        ),
        "weight": 5,
    },
}

GLOBAL_KEYWORDS: dict[str, tuple[int, str]] = {
    "junior": (18, "Acepta perfil junior"),
    "trainee": (18, "Acepta perfil trainee"),
    "aprendiz": (12, "Acepta perfil de entrada"),
    "practicante": (12, "Acepta perfil practicante"),
    "entry level": (12, "Acepta perfil entry level"),
    "sin experiencia": (10, "Acepta perfil sin experiencia"),
    "recien egresado": (10, "Acepta perfil recien egresado"),
    "0 a 1 ano": (10, "Pide poca experiencia"),
    "1 ano": (7, "Pide experiencia inicial"),
    "remoto": (5, "Modalidad remota"),
    "hibrido": (4, "Modalidad hibrida"),
}

NEGATIVE_RULES: dict[str, tuple[int, str]] = {
    "senior": (-26, "Pide perfil senior"),
    "semi senior": (-20, "Pide perfil semi senior"),
    "semisenior": (-20, "Pide perfil semi senior"),
    "lider": (-18, "Pide rol de liderazgo"),
    "arquitecto": (-20, "Pide rol de arquitectura"),
    "4 anos": (-18, "Pide experiencia superior a 3 anos"),
    "5 anos": (-20, "Pide experiencia alta"),
    "mas de 3 anos": (-18, "Pide mas de 3 anos"),
    "experiencia avanzada": (-16, "Exige experiencia avanzada"),
    "ingles avanzado": (-14, "Exige ingles avanzado"),
    "kubernetes avanzado": (-14, "Exige Kubernetes avanzado"),
    "terraform avanzado": (-14, "Exige Terraform avanzado"),
}


def calculate_match(profile: CandidateProfile | None, offer: JobOffer) -> MatchResult:
    text = _normalize(
        " ".join(
            [
                offer.title or "",
                offer.description or "",
                offer.requirements or "",
                offer.location or "",
                offer.modality or "",
            ]
        )
    )
    score = 35
    reasons: list[str] = []

    for keyword, (points, reason) in GLOBAL_KEYWORDS.items():
        if _text_contains(text, keyword):
            score += points
            reasons.append(reason)

    for keyword, (points, reason) in NEGATIVE_RULES.items():
        if _text_contains(text, keyword):
            score += points
            reasons.append(reason)

    active_targets = _resolve_profile_targets(profile, text)
    for target in active_targets:
        rule = TARGET_RULES.get(target)
        if not rule:
            continue
        keywords = rule["keywords"]
        weight = int(rule["weight"])
        matched = [keyword for keyword in keywords if _text_contains(text, keyword)]
        if matched:
            score += min(30, len(matched) * weight)
            reasons.append(f"Coincide con {target}: {', '.join(_pretty_keyword(keyword) for keyword in matched[:4])}")

    if profile:
        profile_text = _normalize(" ".join([profile.skills, profile.summary, profile.target_roles]))
        shared = _count_shared_profile_skills(profile_text, text)
        if shared:
            score += min(10, shared * 2)
            reasons.append("Coincide con habilidades del perfil")

    score = max(0, min(score, 100))
    if not reasons:
        reasons.append("Sin coincidencias claras; revisar manualmente")
    return MatchResult(score=score, reasons=_dedupe_reasons(reasons))


def analyze_relevance_for_target(job, target_role: str) -> RelevanceAnalysis:
    normalized_target = _map_target_alias(target_role) or _normalize(target_role)
    if normalized_target not in TARGET_TITLE_SIGNALS:
        return RelevanceAnalysis(
            relevant=True,
            reasons=["Sin reglas especificas para el target; se conserva para revision"],
            detected_keywords=[],
        )

    title = _normalize(getattr(job, "title", "") or "")
    description = _normalize(getattr(job, "description", "") or "")
    requirements = _normalize(getattr(job, "requirements", "") or "")
    content = _normalize(" ".join([description, requirements]))
    reasons: list[str] = []
    detected_keywords: list[str] = []

    title_senior = _matched_terms(title, GENERAL_SENIOR_SIGNALS)
    if title_senior:
        return RelevanceAnalysis(
            relevant=False,
            reasons=[f"title contiene {title_senior[0]}"],
            detected_keywords=_dedupe_strings(title_senior),
        )

    title_entry = _matched_terms(title, GENERAL_ENTRY_SIGNALS)
    content_senior = _matched_terms(content, GENERAL_SENIOR_SIGNALS)
    title_hits = _matched_terms(title, TARGET_TITLE_SIGNALS[normalized_target])
    content_hits = _matched_terms(content, TARGET_CONTENT_SIGNALS[normalized_target])
    title_admin = _matched_terms(title, ADMINISTRATIVE_ROLE_SIGNALS)

    detected_keywords.extend(title_entry)
    detected_keywords.extend(content_senior)
    detected_keywords.extend(title_hits)
    detected_keywords.extend(content_hits)
    detected_keywords.extend(title_admin)

    if normalized_target == "frontend_junior":
        frontend_false_positive = _matched_terms(title, FRONTEND_FALSE_POSITIVE_SIGNALS)
        detected_keywords.extend(frontend_false_positive)
        if frontend_false_positive and not _has_clear_frontend_signal(title, content):
            return RelevanceAnalysis(
                relevant=False,
                reasons=[f"{frontend_false_positive[0]} no es frontend tecnico"],
                detected_keywords=_dedupe_strings(detected_keywords),
            )

    if title_admin and not title_hits:
        return RelevanceAnalysis(
            relevant=False,
            reasons=["cargo administrativo/no tecnico"],
            detected_keywords=_dedupe_strings(detected_keywords),
        )

    relevant, match_signals = _matches_target_profile(
        normalized_target,
        title=title,
        content=content,
        title_entry=title_entry,
        title_hits=title_hits,
        content_hits=content_hits,
    )
    detected_keywords.extend(match_signals)
    if not relevant:
        return RelevanceAnalysis(
            relevant=False,
            reasons=["no tiene seniales tecnicas suficientes para target"],
            detected_keywords=_dedupe_strings(detected_keywords),
        )

    if title_entry:
        reasons.append(f"title indica perfil de entrada: {title_entry[0]}")
    if content_senior:
        reasons.append(_build_secondary_seniority_reason(content_senior[0], title_entry, title_hits))
    primary_signal = match_signals[0] if match_signals else (title_hits[0] if title_hits else content_hits[0])
    reasons.append(f"coincide con {normalized_target}: {primary_signal}")
    return RelevanceAnalysis(
        relevant=True,
        reasons=_dedupe_reasons(reasons),
        detected_keywords=_dedupe_strings(detected_keywords),
    )


def is_relevant_for_target(job, target_role: str) -> tuple[bool, list[str]]:
    analysis = analyze_relevance_for_target(job, target_role)
    return analysis.relevant, analysis.reasons


def _matches_target_profile(
    normalized_target: str,
    *,
    title: str,
    content: str,
    title_entry: list[str],
    title_hits: list[str],
    content_hits: list[str],
) -> tuple[bool, list[str]]:
    combined_text = _normalize(" ".join([title, content]))
    if normalized_target == "fullstack_junior":
        frontend_hits = _matched_terms(combined_text, FULLSTACK_FRONTEND_SIGNALS)
        backend_hits = _matched_terms(combined_text, FULLSTACK_BACKEND_SIGNALS)
        signals = _dedupe_strings(title_hits + frontend_hits + backend_hits + content_hits)
        relevant = bool(title_hits or (frontend_hits and backend_hits) or (title_entry and frontend_hits and backend_hits))
        return relevant, signals

    if normalized_target == "backend_junior":
        signals = _dedupe_strings(title_hits + content_hits)
        relevant = bool(title_hits or (title_entry and content_hits) or len(content_hits) >= 2)
        return relevant, signals

    if normalized_target == "frontend_junior":
        signals = _dedupe_strings(title_hits + content_hits)
        relevant = bool(title_hits or (title_entry and content_hits) or len(content_hits) >= 2)
        return relevant, signals

    if normalized_target == "devops_trainee":
        signals = _dedupe_strings(title_hits + content_hits)
        relevant = bool(title_hits or (title_entry and content_hits) or len(content_hits) >= 2)
        return relevant, signals

    if normalized_target == "infraestructura_junior":
        signals = _dedupe_strings(title_hits + content_hits)
        relevant = bool(title_hits or (title_entry and content_hits) or len(content_hits) >= 1)
        return relevant, signals

    signals = _dedupe_strings(title_hits + content_hits)
    relevant = bool(title_hits or (title_entry and content_hits) or len(content_hits) >= 2)
    return relevant, signals


def _has_clear_frontend_signal(title: str, content: str) -> bool:
    combined_text = _normalize(" ".join([title, content]))
    technical_terms = _dedupe_strings(
        list(TARGET_TITLE_SIGNALS["frontend_junior"]) + list(TARGET_CONTENT_SIGNALS["frontend_junior"])
    )
    return any(_text_contains(combined_text, term) for term in technical_terms if term not in FRONTEND_FALSE_POSITIVE_SIGNALS)


def _build_secondary_seniority_reason(signal: str, title_entry: list[str], title_hits: list[str]) -> str:
    if title_entry:
        return f"description menciona {signal} pero title es {title_entry[0]}; penalizado, no descartado"
    if title_hits:
        return f"description menciona {signal} pero title es tecnico ({title_hits[0]}); penalizado, no descartado"
    return f"description menciona {signal}; no se descarta solo por texto secundario"


def _resolve_profile_targets(profile: CandidateProfile | None, offer_text: str) -> list[str]:
    targets: list[str] = []
    if profile and profile.target_roles:
        for raw in re.split(r"[,;/]", profile.target_roles):
            normalized = _map_target_alias(raw)
            if normalized:
                targets.append(normalized)
    inferred = _infer_targets_from_offer(offer_text)
    targets.extend(inferred)
    if not targets:
        targets.append("soporte_aplicaciones")
    return _dedupe_strings(targets)


def _infer_targets_from_offer(text: str) -> list[str]:
    inferred: list[str] = []
    if any(_text_contains(text, keyword) for keyword in TARGET_RULES["backend_junior"]["keywords"]):
        inferred.append("backend_junior")
    if any(_text_contains(text, keyword) for keyword in TARGET_RULES["frontend_junior"]["keywords"]):
        inferred.append("frontend_junior")
    if any(_text_contains(text, keyword) for keyword in TARGET_RULES["fullstack_junior"]["keywords"]):
        inferred.append("fullstack_junior")
    if any(_text_contains(text, keyword) for keyword in TARGET_RULES["devops_trainee"]["keywords"]):
        inferred.append("devops_trainee")
    if any(_text_contains(text, keyword) for keyword in TARGET_RULES["soporte_aplicaciones"]["keywords"]):
        inferred.append("soporte_aplicaciones")
    return inferred


def _map_target_alias(value: str) -> str | None:
    normalized = _normalize(value)
    aliases = {
        "devops trainee": "devops_trainee",
        "devops_trainee": "devops_trainee",
        "soporte de aplicaciones": "soporte_aplicaciones",
        "soporte_aplicaciones": "soporte_aplicaciones",
        "infraestructura junior": "infraestructura_junior",
        "infraestructura_junior": "infraestructura_junior",
        "cloud support": "cloud_support",
        "cloud_support": "cloud_support",
        "qa junior": "qa_junior",
        "qa_junior": "qa_junior",
        "backend junior": "backend_junior",
        "backend_junior": "backend_junior",
        "frontend junior": "frontend_junior",
        "frontend_junior": "frontend_junior",
        "fullstack junior": "fullstack_junior",
        "fullstack_junior": "fullstack_junior",
    }
    return aliases.get(normalized)


def _count_shared_profile_skills(profile_text: str, offer_text: str) -> int:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9.+#-]{3,}", profile_text)
        if token not in {"junior", "trainee", "bogota", "colombia"}
    }
    return sum(1 for token in tokens if token in offer_text)


def _pretty_keyword(value: str) -> str:
    replacements = {
        "node.js": "Node.js",
        "next.js": "Next.js",
        "apis rest": "APIs REST",
        "rest api": "REST API",
        "postgresql": "PostgreSQL",
        "mysql": "MySQL",
        "mongodb": "MongoDB",
        "javascript": "JavaScript",
        "typescript": "TypeScript",
        "github": "GitHub",
    }
    return replacements.get(value, value.title())


def _dedupe_reasons(reasons: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for reason in reasons:
        key = reason.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(reason)
    return result


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip().casefold()
    normalized = unicodedata.normalize("NFKD", cleaned)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if _text_contains(text, term)]


def _text_contains(text: str, term: str) -> bool:
    escaped = re.escape(term)
    pattern = rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
    return re.search(pattern, text) is not None
