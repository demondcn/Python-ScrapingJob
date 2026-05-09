from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class MatchResult:
    score: int
    reasons: list[str]


@dataclass(slots=True)
class RelevanceAnalysis:
    relevant: bool
    reasons: list[str]
    detected_keywords: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParsedJobOffer:
    title: str = ""
    company: str = ""
    portal: str = ""
    location: str = ""
    modality: str = ""
    salary: str = ""
    url: str = ""
    description: str = ""
    requirements: str = ""


@dataclass(slots=True)
class ResumeExperience:
    role: str = ""
    company: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    bullets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResumeProject:
    name: str = ""
    role: str = ""
    technologies: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)
    url: str = ""


@dataclass(slots=True)
class ResumeProfile:
    full_name: str = ""
    location: str = ""
    phone: str = ""
    email: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    professional_summary: str = ""
    technical_skills: list[str] = field(default_factory=list)
    soft_skills: list[str] = field(default_factory=list)
    experiences: list[ResumeExperience] = field(default_factory=list)
    projects: list[ResumeProject] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ResumeProfile":
        experiences = [ResumeExperience(**item) for item in data.get("experiences", [])]
        projects = [ResumeProject(**item) for item in data.get("projects", [])]
        return cls(
            full_name=data.get("full_name", ""),
            location=data.get("location", ""),
            phone=data.get("phone", ""),
            email=data.get("email", ""),
            linkedin=data.get("linkedin", ""),
            github=data.get("github", ""),
            portfolio=data.get("portfolio", ""),
            professional_summary=data.get("professional_summary", ""),
            technical_skills=list(data.get("technical_skills", [])),
            soft_skills=list(data.get("soft_skills", [])),
            experiences=experiences,
            projects=projects,
            education=list(data.get("education", [])),
            certifications=list(data.get("certifications", [])),
            languages=list(data.get("languages", [])),
            raw_text=data.get("raw_text", ""),
        )
