from __future__ import annotations

from .selenium_base import SeleniumJobScraper


class LinkedInSeleniumJobScraper(SeleniumJobScraper):
    portal_name = "linkedin_selenium"
    blocked_error_message = "LinkedIn no entregó resultados públicos. Se omite esta fuente."
    captcha_error_message = "LinkedIn mostró captcha o verificación. Se omite esta fuente."
    login_error_message = "LinkedIn requiere inicio de sesión. Se omite esta fuente."

    card_selectors = (
        "ul.jobs-search__results-list li",
        "div.base-search-card",
        "div.job-search-card",
        "li.jobs-search-results__list-item",
    )
    title_selectors = (
        "h3.base-search-card__title",
        ".base-search-card__title",
        "h3",
        "a.base-card__full-link",
    )
    company_selectors = (
        "h4.base-search-card__subtitle",
        ".base-search-card__subtitle",
        ".job-search-card__subtitle",
        "h4",
    )
    location_selectors = (
        ".job-search-card__location",
        ".base-search-card__metadata",
        ".job-search-card__metadata",
    )
    link_selectors = ("a.base-card__full-link[href]", "a[href]")
    posted_selectors = ("time", ".job-search-card__listdate", ".job-search-card__listdate--new")
