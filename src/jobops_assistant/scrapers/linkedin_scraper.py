from __future__ import annotations

from .base_scraper import CaptchaRequiredError, LoginRequiredError, SelectorBasedScraper, SourceBlockedError


class LinkedInJobScraper(SelectorBasedScraper):
    portal_name = "linkedin"
    login_error_message = "LinkedIn no permitió leer resultados públicos. Se omite esta fuente."
    blocked_error_message = "LinkedIn no permitió leer resultados públicos. Se omite esta fuente."
    captcha_error_message = "LinkedIn no permitió leer resultados públicos. Se omite esta fuente."
    card_selectors = (
        "ul.jobs-search__results-list li",
        "div.base-search-card",
        "div.job-search-card",
    )
    title_selectors = ("h3.base-search-card__title", "h3", "a.base-card__full-link")
    company_selectors = ("h4.base-search-card__subtitle", "h4", ".base-search-card__subtitle")
    location_selectors = (".job-search-card__location", ".base-search-card__metadata", ".job-search-card__location")
    link_selectors = ("a.base-card__full-link[href]", "a[href]")
    posted_selectors = ("time", ".job-search-card__listdate", ".job-search-card__listdate--new")

    def _detect_blocked_content(self, html: str) -> None:
        soup = self._soup(html)
        if self._select_cards(soup):
            return
        normalized = self._normalize_text(html)
        if "captcha" in normalized or "security verification" in normalized or "challenge" in normalized:
            raise CaptchaRequiredError(self.captcha_error_message)
        if any(
            token in normalized
            for token in (
                "sign in to view more jobs",
                "let's sign you in",
                "inicia sesion para ver",
                "join linkedin",
                "login",
            )
        ):
            raise LoginRequiredError(self.login_error_message)
        raise SourceBlockedError(self.blocked_error_message)
