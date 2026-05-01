from __future__ import annotations

from .base_scraper import SelectorBasedScraper


class GetOnBoardJobScraper(SelectorBasedScraper):
    portal_name = "getonboard"
    card_selectors = ("article", "div[data-testid='job-card']", "li")
    title_selectors = ("h2 a", "h3 a", ".gb-results-list__title")
    company_selectors = (".company", ".gb-results-list__company", ".subtitle")
    location_selectors = (".location", ".gb-results-list__location", ".remote")
    link_selectors = ("h2 a[href]", "h3 a[href]", "a[href]")
    posted_selectors = (".date", ".gb-results-list__date", "time")
    description_selectors = (".description", ".summary")

