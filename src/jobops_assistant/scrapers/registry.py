from __future__ import annotations

from .computrabajo_playwright_scraper import ComputrabajoPlaywrightJobScraper
from .elempleo_scraper import ElempleoJobScraper
from .getonboard_scraper import GetOnBoardJobScraper
from .indeed_playwright_scraper import IndeedPlaywrightJobScraper
from .linkedin_playwright_scraper import LinkedInPlaywrightJobScraper
from .magneto_scraper import MagnetoJobScraper
from .sena_scraper import SenaJobScraper
from .torre_scraper import TorreJobScraper

SCRAPER_REGISTRY = {
    "linkedin": LinkedInPlaywrightJobScraper,
    "linkedin_playwright": LinkedInPlaywrightJobScraper,
    "computrabajo": ComputrabajoPlaywrightJobScraper,
    "computrabajo_playwright": ComputrabajoPlaywrightJobScraper,
    "elempleo": ElempleoJobScraper,
    "indeed": IndeedPlaywrightJobScraper,
    "indeed_playwright": IndeedPlaywrightJobScraper,
    "magneto": MagnetoJobScraper,
    "torre": TorreJobScraper,
    "getonboard": GetOnBoardJobScraper,
    "sena": SenaJobScraper,
}
DEPRECATED_PORTAL_ALIASES = {
    "linkedin_selenium": "linkedin_playwright",
    "computrabajo_selenium": "computrabajo_playwright",
    "indeed_selenium": "indeed_playwright",
}
AUTHENTICATED_SOURCE_PORTALS = {"linkedin_playwright"}


def get_scraper(portal: str, settings):
    normalized = get_runtime_portal_name(portal)
    scraper_class = SCRAPER_REGISTRY.get(normalized)
    if scraper_class is None:
        raise ValueError(f"Portal no soportado: {portal}")
    return scraper_class(settings)


def list_supported_portals() -> list[str]:
    return sorted(SCRAPER_REGISTRY)


def get_runtime_portal_name(portal: str) -> str:
    normalized = portal.strip().lower()
    return DEPRECATED_PORTAL_ALIASES.get(normalized, normalized)


def is_deprecated_portal_alias(portal: str) -> bool:
    normalized = portal.strip().lower()
    return normalized in DEPRECATED_PORTAL_ALIASES


def source_uses_persistent_auth(portal: str) -> bool:
    return get_runtime_portal_name(portal) in AUTHENTICATED_SOURCE_PORTALS
