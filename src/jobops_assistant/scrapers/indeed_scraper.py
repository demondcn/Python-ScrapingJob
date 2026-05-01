from __future__ import annotations

from .base_scraper import SelectorBasedScraper


class IndeedJobScraper(SelectorBasedScraper):
    portal_name = "indeed"
    card_selectors = ("div.job_seen_beacon", "a.tapItem", "div.slider_container")
    title_selectors = ("h2.jobTitle span", "h2 span", ".jobTitle")
    company_selectors = ("span.companyName", ".companyName")
    location_selectors = ("div.companyLocation", ".companyLocation")
    link_selectors = ("h2 a[href]", "a.tapItem[href]", "a[href]")
    posted_selectors = ("span.date", ".date")
    salary_selectors = (".salary-snippet", ".salaryOnly")

