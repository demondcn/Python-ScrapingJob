from __future__ import annotations

from .computrabajo_scraper import ComputrabajoJobScraper
from .elempleo_scraper import ElempleoJobScraper
from .getonboard_scraper import GetOnBoardJobScraper
from .indeed_scraper import IndeedJobScraper
from .indeed_selenium_scraper import IndeedSeleniumJobScraper
from .linkedin_scraper import LinkedInJobScraper
from .linkedin_selenium_scraper import LinkedInSeleniumJobScraper
from .magneto_scraper import MagnetoJobScraper
from .sena_scraper import SenaJobScraper
from .torre_scraper import TorreJobScraper


SCRAPER_REGISTRY = {
    "linkedin": LinkedInJobScraper,
    "linkedin_selenium": LinkedInSeleniumJobScraper,
    "computrabajo": ComputrabajoJobScraper,
    "elempleo": ElempleoJobScraper,
    "indeed": IndeedJobScraper,
    "indeed_selenium": IndeedSeleniumJobScraper,
    "magneto": MagnetoJobScraper,
    "torre": TorreJobScraper,
    "getonboard": GetOnBoardJobScraper,
    "sena": SenaJobScraper,
}


def get_scraper(portal: str, settings):
    normalized = portal.strip().lower()
    scraper_class = SCRAPER_REGISTRY.get(normalized)
    if scraper_class is None:
        raise ValueError(f"Portal no soportado: {portal}")
    return scraper_class(settings)


def list_supported_portals() -> list[str]:
    return sorted(SCRAPER_REGISTRY)
