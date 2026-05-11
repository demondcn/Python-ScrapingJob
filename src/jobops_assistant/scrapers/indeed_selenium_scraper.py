from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urljoin, urlparse
import re

from bs4 import Tag

from .base_scraper import ScrapedJob
from .selenium_base import SeleniumJobScraper


class IndeedSeleniumJobScraper(SeleniumJobScraper):
    portal_name = "indeed_selenium"
    blocked_error_message = "Indeed no entregó resultados públicos. Se omite esta fuente."
    captcha_error_message = "Indeed mostró Security Check o captcha. Se omite esta fuente."
    login_error_message = "Indeed requiere login para ver resultados. Se omite esta fuente."

    card_selectors = (
        "div.job_seen_beacon",
        "div.slider_container",
        "a.tapItem",
        "div[data-jk]",
    )
    title_selectors = (
        "h2.jobTitle span[title]",
        "h2.jobTitle span",
        "h2 span",
        ".jobTitle",
        "a[data-jk]",
    )
    company_selectors = ("span.companyName", "[data-testid='company-name']", ".companyName")
    location_selectors = ("div.companyLocation", "[data-testid='text-location']", ".companyLocation")
    link_selectors = ("h2 a[href]", "a.tapItem[href]", "a[data-jk][href]", "a[href]")
    posted_selectors = ("span.date", "[data-testid='myJobsStateDate']", ".date")
    salary_selectors = (".salary-snippet", ".salaryOnly", "[data-testid='attribute_snippet_testid']")
    description_selectors = (".job-snippet", "[data-testid='jobsnippet']", ".underShelfFooter")

    def parse_search_results(self, html: str, source) -> list[ScrapedJob]:
        soup = self._soup(html)
        results: list[ScrapedJob] = []
        seen_jks: set[str] = set()
        for card in self._select_cards(soup):
            jk = self._extract_jk(card)
            if not jk:
                print("Indeed Selenium: oferta omitida porque no se pudo extraer URL real/jk")
                continue
            if jk in seen_jks:
                continue
            seen_jks.add(jk)
            title = self._first_text(card, self.title_selectors)
            if not title:
                continue
            company = self._first_text(card, self.company_selectors)
            location = self._first_text(card, self.location_selectors)
            raw_posted_text = self._first_text(card, self.posted_selectors)
            salary = self._first_text(card, self.salary_selectors)
            description = self._first_text(card, self.description_selectors)
            results.append(
                ScrapedJob(
                    title=title,
                    company=company,
                    portal=self.portal_name,
                    location=location,
                    modality=self._infer_modality(location, description),
                    salary=salary,
                    url=f"https://co.indeed.com/viewjob?jk={jk}",
                    description=description,
                    requirements="",
                    published_at=self._parse_published_at(raw_posted_text),
                    found_at=datetime.now(UTC),
                    raw_posted_text=raw_posted_text,
                    source_id=source.id,
                )
            )
        return results

    def normalize_url(self, url: str) -> str:
        jk = self._extract_jk_from_url(url)
        if jk:
            return f"https://co.indeed.com/viewjob?jk={jk}"
        return ""

    def _extract_jk(self, card: Tag) -> str:
        direct_jk = self._clean_jk(str(card.get("data-jk", "") or ""))
        if direct_jk:
            return direct_jk

        for node in card.select("[data-jk]"):
            node_jk = self._clean_jk(str(node.get("data-jk", "") or ""))
            if node_jk:
                return node_jk

        for link in card.select("a[href]"):
            href_jk = self._extract_jk_from_url(str(link.get("href", "") or ""))
            if href_jk:
                return href_jk
        return ""

    def _extract_jk_from_url(self, url: str) -> str:
        if not url:
            return ""
        absolute_url = urljoin("https://co.indeed.com", url)
        parsed = urlparse(absolute_url)
        if not (parsed.netloc.endswith("indeed.com") and parsed.path in {"/rc/clk", "/viewjob"}):
            return ""
        jk_values = parse_qs(parsed.query).get("jk", [])
        if not jk_values:
            return ""
        return self._clean_jk(jk_values[0])

    def _clean_jk(self, value: str) -> str:
        cleaned = (value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9]{8,32}", cleaned):
            return ""
        return cleaned
