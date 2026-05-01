from __future__ import annotations

from .base_scraper import SelectorBasedScraper


class MagnetoJobScraper(SelectorBasedScraper):
    portal_name = "magneto"
    card_selectors = ("article", "div.card", "div.job-card")
    title_selectors = ("h2 a", "h3 a", ".card-title")
    company_selectors = (".company", ".card-company", ".subtitle")
    location_selectors = (".location", ".card-location", ".city")
    link_selectors = ("h2 a[href]", "h3 a[href]", "a[href]")
    posted_selectors = (".date", ".posted", "time")
    description_selectors = (".description", ".summary")

