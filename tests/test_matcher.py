from src.jobops_assistant.matcher import calculate_match
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

