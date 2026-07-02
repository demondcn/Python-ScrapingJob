from __future__ import annotations

import logging

from .base_scraper import BaseJobScraper, ResponseDebugSnapshot
from .computrabajo_playwright_scraper import ComputrabajoPlaywrightJobScraper
from .computrabajo_scraper import ComputrabajoJobScraper
from .elempleo_scraper import ElempleoJobScraper
from .getonboard_scraper import GetOnBoardJobScraper
from .indeed_playwright_scraper import IndeedPlaywrightJobScraper
from .indeed_selenium_scraper import IndeedSeleniumJobScraper
from .linkedin_playwright_scraper import LinkedInPlaywrightJobScraper
from .linkedin_selenium_scraper import LinkedInSeleniumJobScraper
from .magneto_scraper import MagnetoJobScraper
from .sena_scraper import SENA_DEFAULT_SOURCE_URLS, SenaJobScraper
from .torre_scraper import TorreJobScraper

logger = logging.getLogger(__name__)
SCRAPER_ROUTING_MISMATCH = "SCRAPER_ROUTING_MISMATCH"

SCRAPER_REGISTRY = {
    "linkedin": LinkedInPlaywrightJobScraper,
    "linkedin_playwright": LinkedInPlaywrightJobScraper,
    "linkedin_selenium": LinkedInSeleniumJobScraper,
    "computrabajo": ComputrabajoPlaywrightJobScraper,
    "computrabajo_playwright": ComputrabajoPlaywrightJobScraper,
    "computrabajo_selenium": ComputrabajoJobScraper,
    "elempleo": ElempleoJobScraper,
    "indeed": IndeedPlaywrightJobScraper,
    "indeed_playwright": IndeedPlaywrightJobScraper,
    "indeed_selenium": IndeedSeleniumJobScraper,
    "magneto": MagnetoJobScraper,
    "torre": TorreJobScraper,
    "getonboard": GetOnBoardJobScraper,
    "sena": SenaJobScraper,
}
DEPRECATED_PORTAL_ALIASES = {}
AUTHENTICATED_SOURCE_PORTALS = {"linkedin_playwright"}
PORTAL_FLEX_TARGET_FLAGS = {
    "sena": True,
}
PORTAL_ENABLED_FLAGS = {
    "computrabajo": False,
    "computrabajo_playwright": False,
}
PORTAL_REFERENCE_URLS = {
    "computrabajo": (),
    "computrabajo_playwright": (),
    "sena": SENA_DEFAULT_SOURCE_URLS,
}


class DisabledPortalScraper(BaseJobScraper):
    def __init__(self, settings, portal_name: str, *, reason: str, reference_urls: tuple[str, ...] = ()) -> None:
        super().__init__(settings)
        self.portal_name = portal_name
        self.reason = reason
        self.reference_urls = reference_urls

    def scrape(self, source) -> list:
        requested_url = str(getattr(source, "search_url", "") or "")
        self.last_response_debug = ResponseDebugSnapshot(
            requested_url=requested_url,
            status_code=None,
            final_url=requested_url,
            content_type="text/html",
            html="",
            block_reason=self.reason,
        )
        logger.info("[%s] portal deshabilitado en registry: %s", self.portal_name, self.reason)
        return []


def get_scraper(portal: str, settings):
    normalized = get_runtime_portal_name(portal)
    logger.info("[router] portal=%s", normalized)
    if not is_portal_enabled_by_default(normalized):
        return DisabledPortalScraper(
            settings,
            normalized,
            reason="portal deshabilitado por defecto en registry",
            reference_urls=PORTAL_REFERENCE_URLS.get(normalized, ()),
        )
    scraper_class = SCRAPER_REGISTRY.get(normalized)
    if scraper_class is None:
        raise ValueError(f"Portal no soportado: {portal}")
    scraper = scraper_class(settings)
    _validate_scraper_routing(normalized, scraper)
    logger.info("[router] selected_scraper=%s", scraper.__class__.__name__)
    logger.info("[router] engine=%s", getattr(scraper, "engine_name", "unknown"))
    return scraper


def list_supported_portals(*, include_disabled: bool = True) -> list[str]:
    return sorted(
        portal
        for portal in SCRAPER_REGISTRY
        if include_disabled or is_portal_enabled_by_default(portal)
    )


def get_runtime_portal_name(portal: str) -> str:
    normalized = str(portal or "").strip().lower()
    return DEPRECATED_PORTAL_ALIASES.get(normalized, normalized)


def is_deprecated_portal_alias(portal: str) -> bool:
    normalized = portal.strip().lower()
    return normalized in DEPRECATED_PORTAL_ALIASES


def source_uses_persistent_auth(portal: str) -> bool:
    return get_runtime_portal_name(portal) in AUTHENTICATED_SOURCE_PORTALS


def portal_supports_flexible_targets(portal: str) -> bool:
    normalized = get_runtime_portal_name(portal)
    return PORTAL_FLEX_TARGET_FLAGS.get(normalized, False)


def is_portal_enabled_by_default(portal: str) -> bool:
    normalized = get_runtime_portal_name(portal)
    if "ricardo" in normalized:
        return False
    return PORTAL_ENABLED_FLAGS.get(normalized, True)


def get_portal_reference_urls(portal: str) -> tuple[str, ...]:
    normalized = get_runtime_portal_name(portal)
    return PORTAL_REFERENCE_URLS.get(normalized, ())


def _validate_scraper_routing(portal: str, scraper: BaseJobScraper) -> None:
    expected_engine = _expected_engine_for_portal(portal)
    if not expected_engine:
        return
    actual_engine = str(getattr(scraper, "engine_name", "") or "").strip().lower()
    if actual_engine == expected_engine:
        return
    logger.error("[router_error] mismatch_detected=true")
    raise RuntimeError(
        f"{SCRAPER_ROUTING_MISMATCH}: portal={portal} expected_engine={expected_engine} "
        f"actual_engine={actual_engine or 'unknown'} scraper={scraper.__class__.__name__}"
    )


def _expected_engine_for_portal(portal: str) -> str:
    normalized = str(portal or "").strip().lower()
    if "playwright" in normalized:
        return "playwright"
    if "selenium" in normalized:
        return "selenium"
    return ""
