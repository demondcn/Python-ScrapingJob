from __future__ import annotations

from datetime import UTC, datetime

from bs4 import Tag

from .base_scraper import ScrapedJob, SelectorBasedScraper


class ElempleoJobScraper(SelectorBasedScraper):
    portal_name = "elempleo"
    card_selectors = (
        "article",
        "div.result-item",
        "li.result-item",
        "div[class*='job']",
        "div[class*='offer']",
    )
    title_selectors = (
        "h2 a",
        "h3 a",
        "a[href*='/ofertas-empleo/']",
        "a[href*='/oferta-empleo/']",
        ".title",
    )
    company_selectors = (
        ".company",
        ".empresa",
        "[class*='company']",
        "[class*='empresa']",
        ".subtitulo",
    )
    location_selectors = (
        ".location",
        ".ciudad",
        "[class*='location']",
        "[class*='city']",
        "[class*='ciudad']",
        ".subtitulo-2",
    )
    link_selectors = (
        "h2 a[href]",
        "h3 a[href]",
        "a[href*='/ofertas-empleo/']",
        "a[href*='/oferta-empleo/']",
        "a[href]",
    )
    posted_selectors = (
        ".date",
        ".fecha",
        "time",
        "[class*='date']",
        "[class*='fecha']",
    )
    salary_selectors = (
        ".salary",
        ".salario",
        "[class*='salary']",
        "[class*='salario']",
    )
    description_selectors = (
        ".description",
        ".resumen",
        "[class*='description']",
        "[class*='resumen']",
    )
    requirements_selectors = (
        ".contract",
        ".tipo-contrato",
        "[class*='contract']",
        "[class*='contrato']",
    )

    def parse_search_results(self, html: str, source) -> list[ScrapedJob]:
        soup = self._soup(html)
        cards = self._select_cards(soup)
        results: list[ScrapedJob] = []
        for card in cards:
            title = self._extract_title(card)
            url = self._extract_url(card, source)
            if not title or not url:
                continue
            company = self._extract_company(card)
            location = self._extract_location(card)
            salary = self._first_text(card, self.salary_selectors)
            posted = self._extract_posted_text(card)
            contract_type = self._first_text(card, self.requirements_selectors)
            description = self._first_text(card, self.description_selectors)
            modality = self._infer_modality(" ".join(filter(None, (location, description, contract_type, self._clean_text(card.get_text(" ", strip=True))))))
            requirements = contract_type
            results.append(
                ScrapedJob(
                    title=title,
                    company=company,
                    portal=self.portal_name,
                    location=location,
                    modality=modality,
                    salary=salary,
                    url=url,
                    description=description,
                    requirements=requirements,
                    published_at=self._parse_published_at(posted),
                    found_at=datetime.now(UTC),
                    raw_posted_text=posted,
                    source_id=source.id,
                )
            )
        return results

    def _extract_title(self, card: Tag) -> str:
        title = self._first_text(card, self.title_selectors)
        if title:
            return title
        for link in card.select("a[href]"):
            text = self._clean_text(link.get_text(" ", strip=True))
            href = self._clean_text(str(link.get("href", "")))
            if text and ("/ofertas-empleo/" in href or "/oferta-empleo/" in href):
                return text
        return ""

    def _extract_url(self, card: Tag, source) -> str:
        url = self._first_attr(card, self.link_selectors, "href")
        if not url:
            for link in card.select("a[href]"):
                href = self._clean_text(str(link.get("href", "")))
                if "/ofertas-empleo/" in href or "/oferta-empleo/" in href:
                    url = href
                    break
        return self._absolute_url(source, url) if url else ""

    def _extract_company(self, card: Tag) -> str:
        company = self._first_text(card, self.company_selectors)
        if company:
            return company
        for node in card.find_all(["span", "div", "p"]):
            text = self._clean_text(node.get_text(" ", strip=True))
            if not text:
                continue
            lowered = text.casefold()
            if any(token in lowered for token in ("s.a.", "sas", "ltda", "s a s", "tecnologia", "company")):
                return text
        return ""

    def _extract_location(self, card: Tag) -> str:
        location = self._first_text(card, self.location_selectors)
        if location:
            return location
        for node in card.find_all(["span", "div", "p"]):
            text = self._clean_text(node.get_text(" ", strip=True))
            lowered = text.casefold()
            if any(token in lowered for token in ("bogota", "bogotá", "colombia", "remoto", "hibrido", "híbrido", "presencial")):
                return text
        return ""

    def _extract_posted_text(self, card: Tag) -> str:
        posted = self._first_text(card, self.posted_selectors)
        if posted:
            return posted
        for node in card.find_all(["span", "div", "p", "time"]):
            text = self._clean_text(node.get_text(" ", strip=True))
            lowered = text.casefold()
            if any(token in lowered for token in ("hoy", "ayer", "hora", "horas", "día", "dias", "días")):
                return text
        return ""
