from __future__ import annotations

from .base_scraper import SelectorBasedScraper


class SenaJobScraper(SelectorBasedScraper):
    portal_name = "sena"
    card_selectors = ("article", "div.result-item", "li.result-item", "div.card")
    title_selectors = ("h2 a", "h3 a", ".title")
    company_selectors = (".company", ".empresa", ".subtitle")
    location_selectors = (".location", ".ubicacion", ".city")
    link_selectors = ("h2 a[href]", "h3 a[href]", "a[href]")
    posted_selectors = (".date", ".fecha", "time")
    description_selectors = (".description", ".summary")

