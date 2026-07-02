from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import logging
import re
import unicodedata
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from .base_scraper import ResponseDebugSnapshot, ScrapedJob, SelectorBasedScraper

logger = logging.getLogger(__name__)

SENA_DEFAULT_SOURCE_URLS = (
    "https://agenciapublicadeempleo.sena.edu.co/spe-web/spe/public/buscadorVacante?solicitudId=Ingeniero%20de%20software",
    "https://agenciapublicadeempleo.sena.edu.co/spe-web/spe/public/buscadorVacante?solicitudId=Programador%20de%20software",
    "https://agenciapublicadeempleo.sena.edu.co/spe-web/spe/public/buscadorVacante?solicitudId=Tecn%C3%B3logo%20en%20an%C3%A1lisis%20y%20desarrollo%20de%20software",
    "https://agenciapublicadeempleo.sena.edu.co/spe-web/spe/public/buscadorVacante?solicitudId=Dise%C3%B1ador%20de%20soluciones%20de%20software",
    "https://agenciapublicadeempleo.sena.edu.co/spe-web/spe/public/buscadorVacante?solicitudId=Ingeniero%20de%20desarrollo%20software",
)
SENA_TEXT_BLOCK_SELECTORS = (
    "div.tdbuscador",
    "tbody tr",
    "tr",
    "li",
    "div",
)
SENA_TEXT_JOB_KEYWORDS = (
    "software",
    "ingeniero",
    "programador",
    "tecnologo",
)


class SenaJobScraper(SelectorBasedScraper):
    portal_name = "sena"
    card_selectors = (
        "article",
        "div.result-item",
        "li.result-item",
        "div.card",
        ".vacante",
        ".result-item",
        "div[class*='vacante']",
        "li[class*='vacante']",
        "tr",
        "tbody tr",
    )
    title_selectors = (
        "h2 a",
        "h3 a",
        ".title",
        ".vacante__titulo a",
        ".vacante__titulo",
        "a[href*='vacante']",
        "a[href*='detalle']",
    )
    company_selectors = (
        ".company",
        ".empresa",
        ".subtitle",
        ".vacante__empresa",
        "[class*='empresa']",
        "[data-label*='Empresa']",
    )
    location_selectors = (
        ".location",
        ".ubicacion",
        ".city",
        ".vacante__ubicacion",
        "[class*='ubicacion']",
        "[data-label*='Ubicaci']",
    )
    link_selectors = (
        "h2 a[href]",
        "h3 a[href]",
        "a[href*='vacante']",
        "a[href*='detalle']",
        "a[href]",
    )
    posted_selectors = (
        ".date",
        ".fecha",
        "time",
        ".vacante__fecha",
        "[class*='fecha']",
        "[data-label*='Fecha']",
    )
    description_selectors = (
        ".description",
        ".summary",
        ".vacante__descripcion",
        "[class*='descripcion']",
        "[class*='resumen']",
    )

    def fetch_search_results(self, source) -> str:
        urls = self._split_search_urls(str(getattr(source, "search_url", "") or ""))
        if not urls:
            return ""
        html_chunks: list[str] = []
        final_url = urls[-1]
        content_type = "text/html"
        status_code: int | None = None
        for url in urls:
            html = self._request_text(url)
            snapshot = self.get_last_debug_snapshot()
            if snapshot is not None:
                final_url = snapshot.final_url or final_url
                content_type = snapshot.content_type or content_type
                status_code = snapshot.status_code
            html_chunks.append(f"<section data-source-url=\"{url}\">{html}</section>")
        combined_html = "\n".join(html_chunks)
        self.last_response_debug = ResponseDebugSnapshot(
            requested_url="\n".join(urls),
            status_code=status_code,
            final_url=self._clean_text(final_url),
            content_type=content_type,
            html=combined_html,
        )
        return combined_html

    def parse_search_results(self, html: str, source) -> list[ScrapedJob]:
        soup = self._soup(html)
        results = self._parse_text_fallback_results(soup, source)
        if not results:
            results = self._parse_selector_fallback_results(soup, source)
        unique_jobs: list[ScrapedJob] = []
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        for job in results:
            normalized_url = self.normalize_url(job.url)
            normalized_title = self._normalize_text(job.title)
            if not normalized_url or normalized_url in seen_urls or normalized_title in seen_titles:
                continue
            job.url = normalized_url
            seen_urls.add(normalized_url)
            seen_titles.add(normalized_title)
            unique_jobs.append(job)
        return unique_jobs

    def normalize_url(self, url: str) -> str:
        normalized = super().normalize_url(url)
        parsed = urlparse(normalized)
        cleaned_path = parsed.path.split(";", 1)[0]
        cleaned_path = cleaned_path.rstrip("/") or cleaned_path or "/"
        return urlunparse(parsed._replace(path=cleaned_path, params=""))

    def has_public_job_content(self, html: str) -> bool:
        soup = self._soup(html)
        return bool(self._collect_text_blocks(soup)) or bool(self._select_cards(soup))

    def _select_cards(self, soup: BeautifulSoup) -> list[Tag]:
        cards: list[Tag] = []
        seen_nodes: set[int] = set()
        for selector in self.card_selectors:
            for match in soup.select(selector):
                if not isinstance(match, Tag):
                    continue
                if not self._looks_like_job_card(match):
                    continue
                node_id = id(match)
                if node_id in seen_nodes:
                    continue
                seen_nodes.add(node_id)
                cards.append(match)
        return cards

    def _parse_text_fallback_results(self, soup: BeautifulSoup, source) -> list[ScrapedJob]:
        logger.info("[sena] parser_mode=text_fallback")
        blocks = self._collect_text_blocks(soup)
        logger.info("[sena] raw_blocks_found=%s", len(blocks))
        results: list[ScrapedJob] = []
        seen_titles: set[str] = set()
        for block in blocks:
            raw_text = self._clean_text(block.get_text(" ", strip=True))
            if not raw_text or not self._contains_job_keyword(raw_text):
                continue
            title = self._extract_text_title(block, raw_text)
            normalized_title = self._normalize_text(title)
            if not normalized_title or normalized_title in seen_titles:
                continue
            seen_titles.add(normalized_title)
            base_url = self._resolve_block_base_url(block, source)
            url = self._extract_text_url(block, base_url, normalized_title)
            company = self._first_text(block, self.company_selectors)
            location = self._first_text(block, self.location_selectors)
            raw_posted_text = self._extract_text_posted_text(block, raw_text)
            salary = self._extract_text_salary(block, raw_text)
            results.append(
                ScrapedJob(
                    title=title,
                    company=company,
                    portal=self.portal_name,
                    location=location,
                    modality=self._infer_modality(location, raw_text),
                    salary=salary,
                    url=url,
                    description=raw_text,
                    requirements="",
                    published_at=self._parse_published_at(raw_posted_text),
                    found_at=datetime.now(UTC),
                    raw_posted_text=raw_posted_text,
                    source_id=source.id,
                )
            )
        logger.info("[sena] jobs_detected=%s", len(results))
        return results

    def _parse_selector_fallback_results(self, soup: BeautifulSoup, source) -> list[ScrapedJob]:
        results: list[ScrapedJob] = []
        for card in self._select_cards(soup):
            title = self._first_text(card, self.title_selectors)
            url = self._first_attr(card, self.link_selectors, "href")
            if not title:
                continue
            company = self._first_text(card, self.company_selectors)
            location = self._first_text(card, self.location_selectors)
            raw_posted_text = self._first_text(card, self.posted_selectors)
            salary = self._first_text(card, self.salary_selectors)
            description = self._first_text(card, self.description_selectors) or self._clean_text(card.get_text(" ", strip=True))
            base_url = self._resolve_block_base_url(card, source)
            resolved_url = urljoin(base_url, url) if url else self._synthesize_text_url(base_url, self._normalize_text(title))
            results.append(
                ScrapedJob(
                    title=title,
                    company=company,
                    portal=self.portal_name,
                    location=location,
                    modality=self._infer_modality(location, description),
                    salary=salary,
                    url=resolved_url,
                    description=description,
                    requirements="",
                    published_at=self._parse_published_at(raw_posted_text),
                    found_at=datetime.now(UTC),
                    raw_posted_text=raw_posted_text,
                    source_id=source.id,
                )
            )
        return results

    def _collect_text_blocks(self, soup: BeautifulSoup) -> list[Tag]:
        blocks: list[Tag] = []
        seen_texts: set[str] = set()
        for selector in SENA_TEXT_BLOCK_SELECTORS:
            for node in soup.select(selector):
                if not isinstance(node, Tag):
                    continue
                text = self._clean_text(node.get_text(" ", strip=True))
                if not text:
                    continue
                normalized_text = self._normalize_text(text)
                if normalized_text in seen_texts:
                    continue
                if not self._contains_job_keyword(text):
                    continue
                if self._is_wrapper_text_block(node):
                    continue
                seen_texts.add(normalized_text)
                blocks.append(node)
        return blocks

    def _looks_like_job_card(self, node: Tag) -> bool:
        if node.name not in {"article", "div", "li", "tr", "section"}:
            return False
        if node.select_one("a[href*='vacante'], a[href*='detalle'], h2 a[href], h3 a[href]"):
            return True
        classes = " ".join(str(value) for value in node.get("class", [])).casefold()
        if any(token in classes for token in ("vacante", "result", "card")):
            text = self._clean_text(node.get_text(" ", strip=True))
            return bool(text)
        return False

    def _contains_job_keyword(self, text: str) -> bool:
        normalized = self._normalize_keyword_text(text)
        return any(keyword in normalized for keyword in SENA_TEXT_JOB_KEYWORDS)

    def _normalize_keyword_text(self, text: str) -> str:
        decomposed = unicodedata.normalize("NFKD", text or "")
        without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
        return self._normalize_text(without_marks)

    def _is_wrapper_text_block(self, node: Tag) -> bool:
        classes = " ".join(str(value) for value in node.get("class", [])).casefold()
        if "tdbuscador" in classes or "vacante" in classes:
            return False
        node_text = self._clean_text(node.get_text(" ", strip=True))
        node_length = len(node_text)
        if node_length > 900:
            return True
        for child_selector in ("div.tdbuscador", "tbody tr", "tr", "li", "div", "article"):
            for child in node.select(child_selector):
                if not isinstance(child, Tag):
                    continue
                child_text = self._clean_text(child.get_text(" ", strip=True))
                if not child_text or child_text == node_text:
                    continue
                if not self._contains_job_keyword(child_text):
                    continue
                if len(child_text) >= 40:
                    return True
        return False

    def _extract_text_title(self, block: Tag, raw_text: str) -> str:
        for selector in ("h1", "h2", "h3", "h4", "h5", "strong", "b", "a[href]"):
            title = self._first_text(block, (selector,))
            if title and self._contains_job_keyword(title):
                return title[:120]
        return raw_text[:120]

    def _extract_text_url(self, block: Tag, base_url: str, normalized_title: str) -> str:
        for selector in (
            "a.btn[href]",
            "a[href*='solicitud-sintesis']",
            "a[href*='demanda']",
            "a[href]",
        ):
            href = self._first_attr(block, (selector,), "href")
            if href:
                return urljoin(base_url, href)
        return self._synthesize_text_url(base_url, normalized_title)

    def _synthesize_text_url(self, base_url: str, normalized_title: str) -> str:
        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            parsed = urlparse("https://agenciapublicadeempleo.sena.edu.co/spe-web/spe/public/buscadorVacante")
        digest = hashlib.sha1(normalized_title.encode("utf-8")).hexdigest()[:16]
        synthetic_path = f"/spe-web/spe/public/sena-text/{digest}"
        return urlunparse(parsed._replace(path=synthetic_path, params="", query="", fragment=""))

    def _resolve_block_base_url(self, block: Tag, source) -> str:
        section = block.find_parent("section", attrs={"data-source-url": True})
        return str(
            (section.get("data-source-url", "") if section is not None else "")
            or getattr(source, "search_url", "")
            or ""
        )

    def _extract_text_posted_text(self, block: Tag, raw_text: str) -> str:
        posted = self._first_text(block, self.posted_selectors)
        if posted:
            return posted
        match = re.search(r"(Publicado\s+\d{1,2}/\d{1,2}/\d{4})", raw_text, re.IGNORECASE)
        return self._clean_text(match.group(1)) if match else ""

    def _extract_text_salary(self, block: Tag, raw_text: str) -> str:
        salary = self._first_text(block, self.salary_selectors)
        if salary:
            return salary
        match = re.search(r"(Salario(?:\s+no\s+definido|[^.]{0,80}))", raw_text, re.IGNORECASE)
        return self._clean_text(match.group(1)) if match else ""

    def _split_search_urls(self, raw_value: str) -> list[str]:
        parts = [item.strip() for item in re.split(r"[\n;|]+", raw_value or "") if item.strip()]
        return parts or ([raw_value.strip()] if raw_value.strip() else [])
