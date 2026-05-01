from __future__ import annotations

from .schemas import ParsedJobOffer
from .settings import Settings


def read_recent_job_alerts(settings: Settings) -> list[ParsedJobOffer]:
    if not settings.gmail_email or not settings.gmail_app_password:
        return []

    # Implementacion conservadora. La extraccion real desde IMAP se deja
    # preparada para una siguiente iteracion, evitando guardar credenciales
    # o asumir formatos de correo no verificados.
    return []

