from __future__ import annotations

from .indeed_selenium_scraper import IndeedSeleniumJobScraper
from .playwright_base import PlaywrightJobScraper


class IndeedPlaywrightJobScraper(PlaywrightJobScraper, IndeedSeleniumJobScraper):
    portal_name = "indeed_playwright"
    ready_selectors = (
        "div.job_seen_beacon",
        "div.slider_container",
        "a.tapItem",
        "div[data-jk]",
        "body",
    )
