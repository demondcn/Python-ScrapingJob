from types import SimpleNamespace

import pytest

from src.jobops_assistant.matcher import analyze_relevance_for_target, calculate_match, is_relevant_for_target
from src.jobops_assistant.models import CandidateProfile, JobOffer


def test_matcher_rewards_junior_support_stack():
    profile = CandidateProfile(
        full_name="Cris",
        email="cris@example.com",
        phone="3000",
        city="Bogota",
        summary="Interesado en DevOps y backend",
        skills="Python, SQL, Linux, Docker, Git",
        projects="Proyecto personal",
        education="Tecnologo en Desarrollo de Software",
        target_roles="DevOps Trainee, Soporte",
    )
    offer = JobOffer(
        title="Soporte de Aplicaciones Junior",
        company="ABC",
        portal="Portal",
        location="Bogota",
        modality="Hibrido",
        salary="",
        url="https://example.com/job-1",
        description="Soporte a usuarios, tickets, SQL, Docker, Linux y cloud",
        requirements="Perfil junior",
    )
    result = calculate_match(profile, offer)
    assert result.score >= 70
    assert any("junior" in reason.lower() for reason in result.reasons)


def test_matcher_penalizes_senior_requirements():
    offer = JobOffer(
        title="DevOps Senior",
        company="ABC",
        portal="Portal",
        location="Remoto",
        modality="Remoto",
        salary="",
        url="https://example.com/job-2",
        description="Se requiere ingles avanzado y Terraform avanzado",
        requirements="Minimo 5 anos de experiencia",
    )
    result = calculate_match(None, offer)
    assert result.score < 40


def test_matcher_scores_backend_junior_high():
    profile = CandidateProfile(
        full_name="Cris",
        email="cris@example.com",
        phone="3000",
        city="Bogota",
        summary="Interesado en backend",
        skills="Node.js, Express, SQL, PostgreSQL, Git",
        projects="Proyecto backend",
        education="Tecnologo",
        target_roles="backend_junior",
    )
    offer = JobOffer(
        title="Desarrollador Backend Junior",
        company="ABC",
        portal="Portal",
        location="Bogota",
        modality="Remoto",
        salary="",
        url="https://example.com/backend",
        description="Node.js, Express, APIs REST, SQL, PostgreSQL, autenticacion y Docker basico.",
        requirements="Junior con pruebas unitarias.",
    )
    result = calculate_match(profile, offer)
    assert result.score >= 80


def test_matcher_scores_frontend_junior_high():
    profile = CandidateProfile(
        full_name="Cris",
        email="cris@example.com",
        phone="3000",
        city="Bogota",
        summary="Interesado en frontend",
        skills="React, Next.js, JavaScript, TypeScript, GitHub",
        projects="Proyecto frontend",
        education="Tecnologo",
        target_roles="frontend_junior",
    )
    offer = JobOffer(
        title="Desarrollador Frontend Junior",
        company="ABC",
        portal="Portal",
        location="Bogota",
        modality="Remoto",
        salary="",
        url="https://example.com/frontend",
        description="React, Next.js, TypeScript, interfaces, consumo de APIs y diseno responsivo.",
        requirements="Junior",
    )
    result = calculate_match(profile, offer)
    assert result.score >= 80


def test_matcher_scores_fullstack_junior_high():
    profile = CandidateProfile(
        full_name="Cris",
        email="cris@example.com",
        phone="3000",
        city="Bogota",
        summary="Interesado en fullstack",
        skills="React, Node.js, PostgreSQL, Vercel, Git",
        projects="Proyecto fullstack",
        education="Tecnologo",
        target_roles="fullstack_junior",
    )
    offer = JobOffer(
        title="Desarrollador Full Stack Junior",
        company="ABC",
        portal="Portal",
        location="Bogota",
        modality="Hibrido",
        salary="",
        url="https://example.com/fullstack",
        description="React, Node.js, PostgreSQL, panel administrativo, APIs y despliegue en Vercel.",
        requirements="Junior",
    )
    result = calculate_match(profile, offer)
    assert result.score >= 80


def test_is_relevant_backend_accepts_node_sql_junior():
    job = SimpleNamespace(
        title="Backend Junior",
        description="Node.js, APIs REST, SQL y PostgreSQL.",
        requirements="Perfil junior",
        company="ABC",
        raw_posted_text="Hoy",
    )
    relevant, reasons = is_relevant_for_target(job, "backend_junior")
    assert relevant is True
    assert reasons


def test_is_relevant_backend_discards_senior_backend():
    job = SimpleNamespace(
        title="Senior Backend Developer",
        description="Node.js, SQL y microservicios.",
        requirements="5 anos de experiencia",
        company="ABC",
        raw_posted_text="Hoy",
    )
    relevant, reasons = is_relevant_for_target(job, "backend_junior")
    assert relevant is False
    assert any("senior" in reason for reason in reasons)


def test_is_relevant_frontend_accepts_react_junior():
    job = SimpleNamespace(
        title="Frontend Junior",
        description="React, Next.js, TypeScript y diseno responsivo.",
        requirements="Junior",
        company="ABC",
        raw_posted_text="Hoy",
    )
    relevant, _ = is_relevant_for_target(job, "frontend_junior")
    assert relevant is True


def test_is_relevant_fullstack_accepts_react_node_sql():
    job = SimpleNamespace(
        title="Full Stack Developer",
        description="React, Node.js, SQL, panel administrativo y APIs.",
        requirements="1 ano de experiencia",
        company="ABC",
        raw_posted_text="Hoy",
    )
    relevant, reasons = is_relevant_for_target(job, "fullstack_junior")
    assert relevant is True
    assert any("frontend" in reason or "fullstack" in reason for reason in reasons)


def test_is_relevant_devops_accepts_docker_cicd_trainee():
    job = SimpleNamespace(
        title="DevOps Trainee",
        description="Docker, CI/CD, Linux, despliegue y automatizacion.",
        requirements="Trainee",
        company="ABC",
        raw_posted_text="Hoy",
    )
    relevant, _ = is_relevant_for_target(job, "devops_trainee")
    assert relevant is True


def test_is_relevant_soporte_accepts_sql_tickets_soporte():
    job = SimpleNamespace(
        title="Soporte de Aplicaciones",
        description="Soporte a usuarios, tickets, SQL y aplicaciones web.",
        requirements="1 ano",
        company="ABC",
        raw_posted_text="Hoy",
    )
    relevant, _ = is_relevant_for_target(job, "soporte_aplicaciones")
    assert relevant is True


def test_is_relevant_infraestructura_accepts_redes_y_soporte_ti():
    job = SimpleNamespace(
        title="Auxiliar de Infraestructura",
        description="Soporte TI, redes, mantenimiento y configuracion de equipos.",
        requirements="Tecnico de sistemas",
        company="ABC",
        raw_posted_text="Hoy",
    )
    relevant, _ = is_relevant_for_target(job, "infraestructura_junior")
    assert relevant is True


def _job(title: str, description: str = "", requirements: str = ""):
    return SimpleNamespace(
        title=title,
        description=description,
        requirements=requirements,
        company="ABC",
        raw_posted_text="Hoy",
    )


@pytest.mark.parametrize(
    ("title", "description", "requirements", "target_role"),
    [
        ("Ingeniero Devops Junior - Hibrida en Bogota", "Docker, Linux y CI/CD.", "Minimo 5 anos en AWS.", "devops_trainee"),
        ("Work from home junior devops", "Docker, Linux y CI/CD.", "", "devops_trainee"),
        ("Devops junior - republica dominicana", "AWS y pipelines.", "", "devops_trainee"),
        ("Desarrollador Fullstack - developer Fullstack", "React, Node.js y SQL.", "", "fullstack_junior"),
        ("Desarrollador Fullstack - developer Fullstack", "React, Node.js y SQL.", "", "backend_junior"),
        ("Desarrollador Full Stack", "React, APIs y MySQL.", "", "backend_junior"),
        ("Desarrollador Full Stack", "React, APIs y MySQL.", "", "fullstack_junior"),
        ("Desarrollador/a FullStack", "React, Node.js y PostgreSQL.", "", "backend_junior"),
        ("Desarrollador/a FullStack", "React, Node.js y PostgreSQL.", "", "fullstack_junior"),
        ("Desarrollador Python - Bogota", "", "", "backend_junior"),
        ("Practicante Universitario Desarrollo de Software y Bases de Datos", "", "", "backend_junior"),
        ("Aprendiz Desarrollo Fullstack (Front y Back)", "", "", "frontend_junior"),
        ("Aprendiz Desarrollo Fullstack (Front y Back)", "", "", "fullstack_junior"),
        ("Desarrollador Front End Ecommerce", "", "", "frontend_junior"),
        ("Analista 1 Desarrollo Front - Flutter", "", "", "frontend_junior"),
        ("Practicante IT de sistemas", "", "", "infraestructura_junior"),
        ("Aprendiz Tecnico o Tecnologo IT - mantenimiento de equipos", "", "", "infraestructura_junior"),
        ("Estudiante en practica - Area tecnologia de la Informacion", "", "", "infraestructura_junior"),
    ],
)
def test_is_relevant_accepts_real_junior_technical_cases(title, description, requirements, target_role):
    relevant, reasons = is_relevant_for_target(_job(title, description, requirements), target_role)
    assert relevant is True
    assert reasons


@pytest.mark.parametrize(
    ("title", "description", "requirements", "target_role", "expected_reason"),
    [
        ("Desarrollador Senior Java", "Java y APIs.", "", "backend_junior", "title contiene senior"),
        ("DevOps senior", "Docker y AWS.", "", "devops_trainee", "title contiene senior"),
        ("Arquitecto devops", "Cloud y automatizacion.", "", "devops_trainee", "title contiene arquitecto"),
        ("Agente de Front Desk", "", "", "frontend_junior", "front desk no es frontend tecnico"),
        ("Ingeniero NOC Front Office", "", "", "frontend_junior", "front office no es frontend tecnico"),
        ("Recepcionista", "", "", "frontend_junior", "cargo administrativo/no tecnico"),
        ("Asesor Comercial", "", "", "backend_junior", "cargo administrativo/no tecnico"),
        ("Auxiliar de logistica", "", "", "infraestructura_junior", "cargo administrativo/no tecnico"),
        ("Contador publico junior", "", "", "backend_junior", "cargo administrativo/no tecnico"),
        ("Abogado junior", "", "", "backend_junior", "cargo administrativo/no tecnico"),
    ],
)
def test_is_relevant_discards_real_nontechnical_or_senior_cases(
    title,
    description,
    requirements,
    target_role,
    expected_reason,
):
    relevant, reasons = is_relevant_for_target(_job(title, description, requirements), target_role)
    assert relevant is False
    assert expected_reason in reasons


def test_relevance_uses_title_over_description_for_seniority_noise():
    analysis = analyze_relevance_for_target(
        _job(
            "Ingeniero Devops Junior - Hibrida en Bogota",
            "Docker, Linux y CI/CD.",
            "Minimo 5 anos de experiencia en cloud.",
        ),
        "devops_trainee",
    )

    assert analysis.relevant is True
    assert "description menciona 5 anos pero title es junior; penalizado, no descartado" in analysis.reasons


def test_relevance_keeps_frontend_junior_when_secondary_text_mentions_senior():
    analysis = analyze_relevance_for_target(
        _job(
            "Desarrollador web junior presencial",
            "HTML, CSS, JavaScript y React.",
            "Descripcion mezclada con senior en texto secundario.",
        ),
        "frontend_junior",
    )

    assert analysis.relevant is True
    assert any("description menciona senior" in reason for reason in analysis.reasons)


def test_relevance_discards_nontechnical_junior_title_even_if_it_is_entry_level():
    analysis = analyze_relevance_for_target(
        _job(
            "Promotor junior libranza",
            "Gestion comercial y ventas.",
            "Junior",
        ),
        "devops_trainee",
    )

    assert analysis.relevant is False
    assert "cargo administrativo/no tecnico" in analysis.reasons
