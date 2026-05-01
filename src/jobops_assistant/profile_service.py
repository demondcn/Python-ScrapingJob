from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CandidateProfile


def get_profile(session: Session) -> CandidateProfile | None:
    return session.scalar(select(CandidateProfile).limit(1))


def upsert_profile(
    session: Session,
    *,
    full_name: str,
    email: str,
    phone: str,
    city: str,
    summary: str,
    skills: str,
    projects: str,
    education: str,
    target_roles: str,
) -> CandidateProfile:
    profile = get_profile(session)
    if profile is None:
        profile = CandidateProfile(
            full_name=full_name,
            email=email,
            phone=phone,
            city=city,
            summary=summary,
            skills=skills,
            projects=projects,
            education=education,
            target_roles=target_roles,
        )
        session.add(profile)
    else:
        profile.full_name = full_name
        profile.email = email
        profile.phone = phone
        profile.city = city
        profile.summary = summary
        profile.skills = skills
        profile.projects = projects
        profile.education = education
        profile.target_roles = target_roles
    session.commit()
    session.refresh(profile)
    return profile

