from __future__ import annotations

from .base_scraper import SelectorBasedScraper


class TorreJobScraper(SelectorBasedScraper):
    portal_name = "torre"
    card_selectors = ("article", "div[data-testid='job-card']", "div.card")
    title_selectors = ("h2 a", "h3 a", ".title")
    company_selectors = (".company", ".organization", ".subtitle")
    location_selectors = (".location", ".remote", ".city")
    link_selectors = ("h2 a[href]", "h3 a[href]", "a[href]")
    posted_selectors = (".date", ".posted", "time")
    description_selectors = (".description", ".summary")

