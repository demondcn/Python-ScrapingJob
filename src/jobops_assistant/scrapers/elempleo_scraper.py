from __future__ import annotations

from .base_scraper import SelectorBasedScraper


class ElempleoJobScraper(SelectorBasedScraper):
    portal_name = "elempleo"
    card_selectors = ("article", "div.result-item", "li.result-item")
    title_selectors = ("h2 a", "h3 a", ".title")
    company_selectors = (".company", ".empresa", ".subtitulo")
    location_selectors = (".location", ".ciudad", ".subtitulo-2")
    link_selectors = ("h2 a[href]", "h3 a[href]", "a[href]")
    posted_selectors = (".date", ".fecha", "time")
    description_selectors = (".description", ".resumen")

