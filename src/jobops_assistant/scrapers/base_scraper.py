from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
import re

from bs4 import BeautifulSoup, Tag
from dateutil import parser as date_parser
import requests

from ..date_utils import parse_relative_posted_text
from ..models import JobSearchSource
from ..settings import Settings


TRACKING_QUERY_PARAMS = {
    "currentjobid",
    "gh_jid",
    "mc_eid",
    "ref",
    "refid",
    "sessionid",
    "source",
    "trackingid",
    "trk",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


@dataclass(slots=True)
class ScrapedJob:
    title: str
    company: str
    portal: str
    location: str
    modality: str
    salary: str
    url: str
    description: str
    requirements: str
    published_at: datetime | None
    found_at: datetime
    raw_posted_text: str
    source_id: int | None = None


@dataclass(slots=True)
class ResponseDebugSnapshot:
    requested_url: str
    status_code: int | None
    final_url: str
    content_type: str
    html: str
    block_reason: str = ""


class ScraperError(RuntimeError):
    pass


class SourceBlockedError(ScraperError):
    pass


class LoginRequiredError(ScraperError):
    pass


class CaptchaRequiredError(ScraperError):
    pass


class BaseJobScraper:
    portal_name = "base"
    login_error_message = "La fuente requiere inicio de sesion. Se omite esta fuente."
    blocked_error_message = "La fuente bloqueo el acceso publico. Se omite esta fuente."
    captcha_error_message = "La fuente solicito captcha. Se omite esta fuente."

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.last_response_debug: ResponseDebugSnapshot | None = None
        self.session.headers.update(
            {
                "User-Agent": settings.scraper_user_agent,
                "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
            }
        )

    def build_search_url(self, source: JobSearchSource) -> str:
        return source.search_url

    def fetch_search_results(self, source: JobSearchSource) -> str:
        return self._request_text(self.build_search_url(source))

    def parse_search_results(self, html: str, source: JobSearchSource) -> list[ScrapedJob]:
        raise NotImplementedError

    def fetch_job_detail(self, job: ScrapedJob, source: JobSearchSource) -> ScrapedJob:
        return job

    def normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        filtered_query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_QUERY_PARAMS
        ]
        normalized_path = parsed.path.rstrip("/") or parsed.path or "/"
        normalized = parsed._replace(
            query=urlencode(filtered_query, doseq=True),
            fragment="",
            path=normalized_path,
        )
        return urlunparse(normalized)

    def scrape(self, source: JobSearchSource) -> list[ScrapedJob]:
        html = self.fetch_search_results(source)
        results: list[ScrapedJob] = []
        for item in self.parse_search_results(html, source):
            if not item.title or not item.url:
                continue
            item.url = self.normalize_url(item.url)
            job = self.fetch_job_detail(item, source)
            job.url = self.normalize_url(job.url)
            job.portal = self.portal_name
            job.source_id = source.id
            job.found_at = job.found_at or datetime.now(UTC)
            results.append(job)
            if len(results) >= self.settings.max_results_per_source:
                break
        return results

    def _request_text(self, url: str) -> str:
        try:
            response = self.session.get(url, timeout=self.settings.scraper_timeout)
        except requests.Timeout as exc:
            raise ScraperError(f"{self.portal_name}: timeout al consultar {url}") from exc
        except requests.RequestException as exc:
            raise ScraperError(f"{self.portal_name}: error de red al consultar {url}: {exc}") from exc

        text = response.text
        self.last_response_debug = ResponseDebugSnapshot(
            requested_url=url,
            status_code=response.status_code,
            final_url=self._clean_text(getattr(response, "url", "") or url),
            content_type=self._clean_text(getattr(response, "headers", {}).get("Content-Type", "")),
            html=text,
        )
        if response.status_code == 403:
            self._set_block_reason("status 403")
            raise SourceBlockedError(self.blocked_error_message)
        if response.status_code == 429:
            self._set_block_reason("status 429")
            raise SourceBlockedError(f"{self.portal_name}: el portal devolvio 429. Se omite esta fuente.")
        response.raise_for_status()
        self._detect_blocked_content(text)
        return text

    def _detect_blocked_content(self, html: str) -> None:
        soup = self._soup(html)
        normalized = self._normalize_text(soup.get_text(" ", strip=True))
        html_normalized = self._normalize_text(html)
        if self._has_strong_block_signal(soup, normalized, html_normalized):
            raise CaptchaRequiredError(self.captcha_error_message)
        if any(
            token in normalized
            for token in (
                "inicia sesion para continuar",
                "iniciar sesion para continuar",
                "sign in to continue",
                "login required",
                "accede con tu cuenta",
            )
        ):
            self._set_block_reason("login requerido detectado en el contenido")
            raise LoginRequiredError(self.login_error_message)

    def _has_strong_block_signal(self, soup: BeautifulSoup, visible_text: str, html_text: str) -> bool:
        text_signals = {
            "verifica que no eres un robot": "texto visible de captcha",
            "verify you are human": "texto visible de captcha",
            "access denied": "access denied visible",
            "forbidden": "forbidden visible",
            "attention required": "cloudflare challenge visible",
            "enable javascript and cookies to continue": "cloudflare challenge visible",
        }
        for token, reason in text_signals.items():
            if token in visible_text:
                self._set_block_reason(reason)
                return True

        strong_selectors = {
            "iframe[src*='recaptcha']": "iframe recaptcha visible",
            ".g-recaptcha": "widget recaptcha visible",
            ".cf-turnstile": "turnstile visible",
            "iframe[src*='turnstile']": "turnstile visible",
            "form[action*='captcha']": "formulario captcha visible",
            "input[name*='captcha']": "input captcha visible",
            "#cf-challenge-running": "cloudflare challenge visible",
            "#challenge-running": "challenge visible",
        }
        for selector, reason in strong_selectors.items():
            if soup.select_one(selector):
                if self.has_public_job_content(html_text):
                    continue
                self._set_block_reason(reason)
                return True

        if "cf-browser-verification" in html_text or "cf-chl-" in html_text:
            if not self.has_public_job_content(html_text):
                self._set_block_reason("cloudflare challenge embebido")
                return True
        return False

    def has_public_job_content(self, html: str) -> bool:
        return False

    def _set_block_reason(self, reason: str) -> None:
        if self.last_response_debug is not None:
            self.last_response_debug.block_reason = reason

    def get_last_debug_snapshot(self) -> ResponseDebugSnapshot | None:
        return self.last_response_debug

    def _soup(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")

    def _absolute_url(self, source: JobSearchSource, url: str) -> str:
        return urljoin(source.search_url, url)

    def _clean_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    def _normalize_text(self, value: str) -> str:
        return self._clean_text(value).casefold()

    def _first_text(self, node: Tag, selectors: Iterable[str]) -> str:
        for selector in selectors:
            match = node.select_one(selector)
            if match:
                text = self._clean_text(match.get_text(" ", strip=True))
                if text:
                    return text
        return ""

    def _first_attr(self, node: Tag, selectors: Iterable[str], attr: str) -> str:
        for selector in selectors:
            match = node.select_one(selector)
            if match and match.has_attr(attr):
                value = self._clean_text(str(match.get(attr, "")))
                if value:
                    return value
        return ""

    def _infer_modality(self, *values: str) -> str:
        text = self._normalize_text(" ".join(values))
        if any(token in text for token in ("remoto", "remote", "home office")):
            return "Remoto"
        if any(token in text for token in ("hibrido", "híbrido", "hybrid")):
            return "Híbrido"
        if any(token in text for token in ("presencial", "onsite", "on-site")):
            return "Presencial"
        return ""

    def _parse_published_at(self, raw_value: str) -> datetime | None:
        text = self._normalize_text(raw_value)
        if not text:
            return None

        relative_parsed = parse_relative_posted_text(raw_value, now=datetime.now(UTC))
        if relative_parsed is not None:
            return relative_parsed

        try:
            parsed = date_parser.parse(raw_value, fuzzy=True, dayfirst=True)
        except (ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        else:
            parsed = parsed.astimezone(UTC)
        return parsed


class SelectorBasedScraper(BaseJobScraper):
    card_selectors: tuple[str, ...] = ()
    title_selectors: tuple[str, ...] = ()
    company_selectors: tuple[str, ...] = ()
    location_selectors: tuple[str, ...] = ()
    link_selectors: tuple[str, ...] = ()
    posted_selectors: tuple[str, ...] = ()
    salary_selectors: tuple[str, ...] = ()
    description_selectors: tuple[str, ...] = ()
    requirements_selectors: tuple[str, ...] = ()

    def parse_search_results(self, html: str, source: JobSearchSource) -> list[ScrapedJob]:
        soup = self._soup(html)
        cards = self._select_cards(soup)
        results: list[ScrapedJob] = []
        for card in cards:
            title = self._first_text(card, self.title_selectors)
            url = self._first_attr(card, self.link_selectors, "href")
            if not title or not url:
                continue
            company = self._first_text(card, self.company_selectors)
            location = self._first_text(card, self.location_selectors)
            raw_posted_text = self._first_text(card, self.posted_selectors)
            salary = self._first_text(card, self.salary_selectors)
            description = self._first_text(card, self.description_selectors)
            requirements = self._first_text(card, self.requirements_selectors)
            modality = self._infer_modality(location, description, requirements)
            results.append(
                ScrapedJob(
                    title=title,
                    company=company,
                    portal=self.portal_name,
                    location=location,
                    modality=modality,
                    salary=salary,
                    url=self._absolute_url(source, url),
                    description=description,
                    requirements=requirements,
                    published_at=self._parse_published_at(raw_posted_text),
                    found_at=datetime.now(UTC),
                    raw_posted_text=raw_posted_text,
                    source_id=source.id,
                )
            )
        return results

    def _select_cards(self, soup: BeautifulSoup) -> list[Tag]:
        for selector in self.card_selectors:
            matches = soup.select(selector)
            if matches:
                return [match for match in matches if isinstance(match, Tag)]
        return []

    def has_public_job_content(self, html: str) -> bool:
        return bool(self._select_cards(self._soup(html)))
