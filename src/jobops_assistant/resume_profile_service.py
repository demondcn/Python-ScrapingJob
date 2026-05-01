from __future__ import annotations

import json
from pathlib import Path

from .schemas import ResumeProfile


DEFAULT_RESUME_PROFILE_PATH = Path("data/resume_profile.json")


def save_resume_profile(profile: ResumeProfile, path: Path | None = None) -> Path:
    target_path = path or DEFAULT_RESUME_PROFILE_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target_path


def load_resume_profile(path: Path | None = None) -> ResumeProfile:
    target_path = path or DEFAULT_RESUME_PROFILE_PATH
    if not target_path.exists():
        raise FileNotFoundError(f"No existe el perfil de HV importado en: {target_path}")
    data = json.loads(target_path.read_text(encoding="utf-8"))
    return ResumeProfile.from_dict(data)

