from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from time import sleep
from types import SimpleNamespace
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

try:
    from lxml import etree as lxml_etree
    from lxml import html as lxml_html
except ImportError:  # pragma: no cover - keep a safe fallback if lxml is unavailable.
    lxml_etree = None
    lxml_html = None

from .base_scraper import (
    CaptchaRequiredError,
    LoginRequiredError,
    ResponseDebugSnapshot,
    ScrapedJob,
    SourceBlockedError,
)
from .selenium_base import SeleniumJobScraper


logger = logging.getLogger(__name__)

COMPUTRABAJO_STATE_BLOCKED = "blocked"
COMPUTRABAJO_STATE_SCRAPER_BROKEN = "scraper_broken"
COMPUTRABAJO_STATE_NO_RESULTS = "no_results"
COMPUTRABAJO_STATE_OK = "ok"

COMPUTRABAJO_BLOCK_TEXT_SIGNALS = (
    "captcha",
    "recaptcha",
    "security check",
    "security verification",
    "access denied",
    "robot check",
    "forbidden",
)
COMPUTRABAJO_CAPTCHA_TEXT_SIGNALS = (
    "captcha",
    "recaptcha",
    "security check",
    "security verification",
    "robot check",
)
COMPUTRABAJO_LOGIN_TEXT_SIGNALS = (
    "inicia sesion",
    "iniciar sesion",
    "inicia sesión",
    "iniciar sesión",
    "sign in",
    "login required",
    "accede para continuar",
    "ingresa para continuar",
)
COMPUTRABAJO_BLOCK_URL_SIGNALS = (
    "login",
    "signin",
    "sign-in",
    "auth",
    "blocked",
    "captcha",
)
COMPUTRABAJO_NO_RESULTS_SIGNALS = (
    "no hay ofertas",
    "no se encontraron ofertas",
    "no se encontraron resultados",
    "sin resultados",
    "ninguna oferta",
    "0 ofertas",
    "no encontramos ofertas",
)
COMPUTRABAJO_PRIMARY_CARD_SELECTORS = (
    "div.box_offer",
    ".js-card",
    "article",
    "[data-id]",
    "div.ofer-descripcion",
    "div.js-o-link",
)
COMPUTRABAJO_ALT_CARD_SELECTORS = (
    "div[class*='offer']",
    "li[class*='offer']",
    "div[class*='job']",
    "li[class*='job']",
    "div[class*='result']",
    "li[class*='result']",
    "div[class*='vacan']",
    "li[class*='vacan']",
)
COMPUTRABAJO_JOB_LINK_PATTERNS = (
    "/oferta-de-trabajo",
    "/trabajo-",
    "/oferta/",
    "/ofertas/",
    "oferta",
)
COMPUTRABAJO_JOB_CONTAINER_TAGS = {"article", "div", "li", "section"}
COMPUTRABAJO_LOCATION_HINTS = (
    "bogot",
    "medell",
    "cali",
    "barranquilla",
    "colombia",
    "remot",
    "hibrid",
    "presencial",
)
COMPUTRABAJO_POSTED_HINTS = (
    "publicad",
    "hace",
    "hoy",
    "ayer",
    "hora",
    "dia",
    "semana",
    "mes",
)
COMPUTRABAJO_COMPANY_HINTS = (
    " sas",
    "s.a",
    "ltda",
    "empresa",
    "tecnolog",
    "solutions",
    "services",
)
COMPUTRABAJO_READY_SELECTORS = (
    "div.box_offer",
    ".js-card",
    "article",
    "[data-id]",
    "div.ofer-descripcion",
    "div.js-o-link",
    "a[href*='/oferta-de-trabajo']",
    "a[href*='/trabajo-']",
    "a[href*='/ofertas/']",
    ".box_detail",
    "main",
    "body",
)


def extract_job_cards_robust(html: str, soup: BeautifulSoup) -> list[Tag]:
    return _extract_job_cards_robust(html, soup, log_counts=True)


def _extract_job_cards_robust(html: str, soup: BeautifulSoup, *, log_counts: bool) -> list[Tag]:
    primary_cards = _extract_cards_from_selectors(soup, COMPUTRABAJO_PRIMARY_CARD_SELECTORS)
    alt_cards = _extract_cards_from_selectors(soup, COMPUTRABAJO_ALT_CARD_SELECTORS)
    xpath_cards = _extract_cards_from_xpath(html, soup)
    fallback_cards = _extract_cards_from_fallback_links(soup)

    if log_counts:
        logger.info("[computrabajo] selector=primary found=%s", len(primary_cards))
        logger.info("[computrabajo] selector=alt found=%s", len(alt_cards))
        logger.info("[computrabajo] selector=xpath found=%s", len(xpath_cards))
        logger.info("[computrabajo] selector=fallback found=%s", len(fallback_cards))

    cards = _dedupe_job_nodes(primary_cards + alt_cards + xpath_cards + fallback_cards)
    if log_counts:
        logger.info("[computrabajo] total_jobs_extracted=%s", len(cards))
    return cards


def _extract_cards_from_selectors(soup: BeautifulSoup, selectors: tuple[str, ...]) -> list[Tag]:
    candidates: list[Tag] = []
    for selector in selectors:
        for match in soup.select(selector):
            if isinstance(match, Tag):
                candidates.append(match)
    return _collect_nodes_from_candidates(candidates)


def _extract_cards_from_xpath(html: str, soup: BeautifulSoup) -> list[Tag]:
    if not html or lxml_html is None:
        return []

    try:
        tree = lxml_html.fromstring(html)
    except (lxml_etree.ParserError, TypeError, ValueError):
        return []

    matches = tree.xpath(
        "//a[@href and ("
        "contains(translate(@href, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '/oferta-de-trabajo')"
        " or contains(translate(@href, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '/trabajo-')"
        " or contains(translate(@href, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '/oferta/')"
        " or contains(translate(@href, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '/ofertas/')"
        " or contains(translate(@href, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'oferta')"
        ")]"
    )
    if not matches:
        return []

    index = _index_relevant_anchors(soup)
    cards: list[Tag] = []
    seen_hrefs: set[str] = set()
    for match in matches:
        href = str(match.get("href", "")).strip()
        key = _normalize_job_href(href)
        if not key or key in seen_hrefs:
            continue
        seen_hrefs.add(key)
        for anchor in index.get(key, []):
            cards.append(_find_job_container(anchor))
    return _dedupe_job_nodes(cards)


def _extract_cards_from_fallback_links(soup: BeautifulSoup) -> list[Tag]:
    cards = [_find_job_container(anchor) for anchor in _iter_relevant_anchors(soup)]
    return _dedupe_job_nodes(cards)


def _collect_nodes_from_candidates(candidates: list[Tag]) -> list[Tag]:
    nodes: list[Tag] = []
    for candidate in candidates:
        anchors = _iter_relevant_anchors(candidate)
        if not anchors:
            fallback_anchor = _first_fallback_anchor(candidate)
            if fallback_anchor is not None:
                nodes.append(_find_job_container(fallback_anchor))
            continue
        if len(anchors) == 1:
            nodes.append(candidate)
            continue
        for anchor in anchors:
            nodes.append(_find_job_container(anchor))
    return _dedupe_job_nodes(nodes)


def _iter_relevant_anchors(node: BeautifulSoup | Tag) -> list[Tag]:
    if not isinstance(node, (BeautifulSoup, Tag)):
        return []

    candidates: list[Tag] = []
    if isinstance(node, Tag) and node.name == "a" and node.has_attr("href"):
        candidates.append(node)
    candidates.extend(match for match in node.select("a[href]") if isinstance(match, Tag))

    anchors: list[Tag] = []
    seen_hrefs: set[str] = set()
    for anchor in candidates:
        href = str(anchor.get("href", "")).strip()
        key = _normalize_job_href(href)
        if not key or key in seen_hrefs or not _is_relevant_job_href(href):
            continue
        seen_hrefs.add(key)
        anchors.append(anchor)
    return anchors


def _find_job_container(anchor: Tag) -> Tag:
    fallback = anchor
    for parent in anchor.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name not in COMPUTRABAJO_JOB_CONTAINER_TAGS:
            continue
        relevant_links = _iter_relevant_anchors(parent)
        if len(relevant_links) == 1:
            fallback = parent
            continue
        if fallback is anchor and len(relevant_links) <= 3:
            fallback = parent
            continue
        if fallback is not anchor:
            break
    return fallback


def _index_relevant_anchors(soup: BeautifulSoup) -> dict[str, list[Tag]]:
    index: dict[str, list[Tag]] = {}
    for anchor in _iter_relevant_anchors(soup):
        key = _normalize_job_href(str(anchor.get("href", "")))
        if not key:
            continue
        index.setdefault(key, []).append(anchor)
    return index


def _dedupe_job_nodes(nodes: list[Tag]) -> list[Tag]:
    deduped: dict[str, Tag] = {}
    for node in nodes:
        anchor = _first_relevant_anchor(node)
        if anchor is None:
            continue
        key = _normalize_job_href(str(anchor.get("href", "")))
        if not key or key in deduped:
            continue
        deduped[key] = node
    return list(deduped.values())


def _first_relevant_anchor(node: BeautifulSoup | Tag) -> Tag | None:
    anchors = _iter_relevant_anchors(node)
    if anchors:
        return anchors[0]
    return _first_fallback_anchor(node)


def _first_fallback_anchor(node: BeautifulSoup | Tag) -> Tag | None:
    if not isinstance(node, (BeautifulSoup, Tag)):
        return None
    if isinstance(node, Tag) and node.name == "a" and node.has_attr("href"):
        href = str(node.get("href", "")).strip()
        if _normalize_job_href(href):
            return node
    if isinstance(node, Tag) and not _looks_like_known_job_container(node):
        return None
    match = node.select_one("a[href]")
    if not isinstance(match, Tag):
        return None
    href = str(match.get("href", "")).strip()
    if not _normalize_job_href(href):
        return None
    return match


def _looks_like_known_job_container(node: Tag) -> bool:
    if node.name == "article" or node.has_attr("data-id"):
        return True
    classes = " ".join(str(value) for value in node.get("class", [])).casefold()
    return any(
        token in classes
        for token in (
            "box_offer",
            "js-card",
            "ofer-descripcion",
            "offer",
            "job",
            "result",
        )
    )


def _normalize_job_href(href: str) -> str:
    cleaned = re.sub(r"\s+", "", href or "")
    if not cleaned or cleaned.startswith("#") or cleaned.lower().startswith("javascript:"):
        return ""
    parsed = urlparse(cleaned)
    path = (parsed.path or "").rstrip("/")
    if not path and not parsed.netloc:
        return ""
    return f"{parsed.netloc.casefold()}{path.casefold()}"


def _is_relevant_job_href(href: str) -> bool:
    normalized_href = (href or "").casefold()
    if not _normalize_job_href(href):
        return False
    return any(pattern in normalized_href for pattern in COMPUTRABAJO_JOB_LINK_PATTERNS)


def _has_relevant_job_links(html: str) -> bool:
    if not html:
        return False
    soup = BeautifulSoup(html, "lxml")
    return bool(_extract_cards_from_fallback_links(soup))


def detect_blocking_state(response, html: str, job_cards) -> str:
    status_code = getattr(response, "status_code", None)
    final_url_path = _normalize_response_url_path(response)
    normalized_html = _normalize_computrabajo_text(html)
    cards_count = len(job_cards or [])
    has_relevant_job_links = cards_count > 0 or _has_relevant_job_links(html)

    if status_code in {403, 429}:
        return COMPUTRABAJO_STATE_BLOCKED
    if any(token in normalized_html for token in COMPUTRABAJO_BLOCK_TEXT_SIGNALS):
        return COMPUTRABAJO_STATE_BLOCKED
    if not has_relevant_job_links and any(token in normalized_html for token in COMPUTRABAJO_LOGIN_TEXT_SIGNALS):
        return COMPUTRABAJO_STATE_BLOCKED
    if not has_relevant_job_links and any(token in final_url_path for token in COMPUTRABAJO_BLOCK_URL_SIGNALS):
        return COMPUTRABAJO_STATE_BLOCKED

    if not has_relevant_job_links:
        if any(token in normalized_html for token in COMPUTRABAJO_NO_RESULTS_SIGNALS):
            return COMPUTRABAJO_STATE_NO_RESULTS
        return COMPUTRABAJO_STATE_SCRAPER_BROKEN

    return COMPUTRABAJO_STATE_OK


def _normalize_computrabajo_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _detect_blocking_reason(response, html: str) -> str:
    status_code = getattr(response, "status_code", None)
    if status_code == 403:
        return "status 403"
    if status_code == 429:
        return "status 429"

    normalized_html = _normalize_computrabajo_text(html)
    for token in COMPUTRABAJO_BLOCK_TEXT_SIGNALS:
        if token in normalized_html:
            return token
    for token in COMPUTRABAJO_LOGIN_TEXT_SIGNALS:
        if token in normalized_html:
            return token

    final_url_path = _normalize_response_url_path(response)
    for token in COMPUTRABAJO_BLOCK_URL_SIGNALS:
        if token in final_url_path:
            return f"redirect {token}"
    return "bloqueo detectado"


def _normalize_response_url_path(response) -> str:
    final_url = getattr(response, "url", "") or ""
    parsed = urlparse(final_url)
    return _normalize_computrabajo_text(f"{parsed.netloc}{parsed.path}")


class ComputrabajoJobScraper(SeleniumJobScraper):
    portal_name = "computrabajo"
    blocked_error_message = "Computrabajo bloqueo el acceso publico. Se omite esta fuente."
    captcha_error_message = "Computrabajo solicito captcha o verificacion. Se omite esta fuente."
    login_error_message = "Computrabajo requiere login para continuar. Se omite esta fuente."
    card_selectors = COMPUTRABAJO_PRIMARY_CARD_SELECTORS
    title_selectors = (
        "h2 a",
        "h2",
        ".title",
        ".box_offer h2 a",
        ".js-card h2 a",
        "article h2 a",
        "[data-id] h2 a",
        "div.ofer-descripcion a[href]",
    )
    company_selectors = (
        ".fs16.fc_base.mt5",
        ".it-company",
        ".company",
        ".box_offer [class*='company']",
        ".js-card [class*='company']",
        "[data-id] [class*='company']",
        "[class*='empresa']",
    )
    location_selectors = (
        ".fs13.fc_aux.mt15",
        ".it-location",
        ".location",
        ".box_offer [class*='location']",
        ".js-card [class*='location']",
        "[data-id] [class*='location']",
        "[class*='ciudad']",
        "[class*='ubicacion']",
    )
    link_selectors = (
        "h2 a[href]",
        "a.js-o-link[href]",
        ".box_offer a[href]",
        ".js-card a[href]",
        "div.ofer-descripcion a[href]",
        "a[href]",
    )
    posted_selectors = (".fc_aux.fs13", ".it-posted", "time")
    description_selectors = (
        ".mb10",
        ".description",
        "div.ofer-descripcion",
        ".box_offer p",
        ".js-card p",
    )
    salary_selectors = (
        ".tag.base.mb10",
        ".salary",
        ".it-salary",
        ".box_offer [class*='salary']",
        ".js-card [class*='salary']",
        "[data-id] [class*='salary']",
    )

    def __init__(self, settings, driver_factory=None, *, log_selenium: bool = True) -> None:
        super().__init__(settings, driver_factory=driver_factory, log_selenium=log_selenium)
        self._active_driver = None

    def scrape(self, source) -> list[ScrapedJob]:
        requested_url = self.build_search_url(source)
        if not self.settings.enable_selenium:
            self._record_search_snapshot(
                requested_url,
                requested_url,
                "",
                status_code=None,
                block_reason="selenium desactivado",
            )
            response = self._make_state_response(requested_url, None)
            self._log_search_state(COMPUTRABAJO_STATE_SCRAPER_BROKEN, response, "", [])
            logger.warning("[%s] selenium_disabled url=%s", self.portal_name, requested_url)
            return []

        try:
            driver = self._build_driver()
        except Exception as exc:
            logger.warning("[%s] selenium_driver_error=%s", self.portal_name, exc)
            self._handle_search_failure(requested_url, f"selenium driver error: {exc}")
            return []

        self._active_driver = driver
        try:
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
        finally:
            self._active_driver = None
            try:
                driver.quit()
            except Exception:
                pass

    def fetch_search_results(self, source) -> str:
        requested_url = self.build_search_url(source)
        if not self.settings.enable_selenium:
            self._record_search_snapshot(
                requested_url,
                requested_url,
                "",
                status_code=None,
                block_reason="selenium desactivado",
            )
            response = self._make_state_response(requested_url, None)
            self._log_search_state(COMPUTRABAJO_STATE_SCRAPER_BROKEN, response, "", [])
            logger.warning("[%s] selenium_disabled url=%s", self.portal_name, requested_url)
            return ""

        driver = self._active_driver
        owned_driver = False
        if driver is None:
            try:
                driver = self._build_driver()
                owned_driver = True
            except Exception as exc:
                logger.warning("[%s] selenium_driver_error=%s", self.portal_name, exc)
                return self._handle_search_failure(requested_url, f"selenium driver error: {exc}")

        try:
            html, final_url, status_code = self._load_rendered_html(driver, requested_url)
            self._record_search_snapshot(requested_url, final_url, html, status_code=status_code)
            logger.info("[computrabajo] extracting_jobs_from_rendered_html")
            soup = BeautifulSoup(html, "html.parser")
            job_cards = extract_job_cards_robust(html, soup)
            response = self._make_state_response(final_url, status_code)
            state = detect_blocking_state(response, html, job_cards)
            self._log_search_state(state, response, html, job_cards)

            if state == COMPUTRABAJO_STATE_BLOCKED:
                self._raise_blocked_search_state(response, html)
            return html
        except (CaptchaRequiredError, LoginRequiredError, SourceBlockedError):
            raise
        except Exception as exc:
            current_url = getattr(driver, "current_url", "") or requested_url
            current_html = getattr(driver, "page_source", "") or ""
            logger.warning("[%s] selenium_search_error=%s", self.portal_name, exc)
            return self._handle_search_failure(
                requested_url,
                f"selenium search error: {exc}",
                final_url=current_url,
                html=current_html,
            )
        finally:
            if owned_driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def parse_search_results(self, html: str, source) -> list[ScrapedJob]:
        soup = self._soup(html)
        cards = _extract_job_cards_robust(html, soup, log_counts=False)
        results: list[ScrapedJob] = []
        seen_urls: set[str] = set()
        found_at = datetime.now(UTC)

        for card in cards:
            title = self._extract_card_title(card)
            url = self._extract_card_url(card, source)
            if not title or not url:
                continue

            normalized_url = self.normalize_url(url)
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)

            company = self._extract_card_company(card)
            location = self._extract_card_location(card)
            raw_posted_text = self._extract_card_posted_text(card)
            salary = self._extract_card_salary(card)
            description = self._extract_card_description(card)
            requirements = self._extract_card_requirements(card)
            modality = self._infer_modality(
                location,
                description,
                requirements,
                self._clean_text(card.get_text(" ", strip=True)),
            )
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
                    published_at=self._parse_published_at(raw_posted_text),
                    found_at=found_at,
                    raw_posted_text=raw_posted_text,
                    source_id=source.id,
                )
            )
        return results

    def has_public_job_content(self, html: str) -> bool:
        soup = self._soup(html)
        if _extract_job_cards_robust(html, soup, log_counts=False):
            return True
        if soup.select_one(".box_detail, [data-cy='job-description'], script[type='application/ld+json']"):
            normalized = self._normalize_text(soup.get_text(" ", strip=True))
            return "oferta" in normalized or "trabajo" in normalized or bool(soup.select_one("h1"))
        return False

    def _select_cards(self, soup: BeautifulSoup) -> list[Tag]:
        return _extract_job_cards_robust(str(soup), soup, log_counts=False)

    def fetch_job_detail(self, job: ScrapedJob, source) -> ScrapedJob:
        if not self.settings.enable_selenium:
            return job

        driver = self._active_driver
        owned_driver = False
        if driver is None:
            try:
                driver = self._build_driver()
                owned_driver = True
            except Exception as exc:
                logger.warning("[%s] detalle omitido por error: %s (%s)", self.portal_name, job.url, exc)
                return job

        try:
            driver.set_page_load_timeout(self.settings.selenium_page_load_timeout)
            driver.get(job.url)
            self._wait_for_rendered_dom(driver, (".box_detail", "h1", "body"))
            html = getattr(driver, "page_source", "") or ""
            final_url = getattr(driver, "current_url", "") or job.url
            if not html:
                raise RuntimeError("page_source vacio en detalle")
            self.last_response_debug = ResponseDebugSnapshot(
                requested_url=job.url,
                status_code=200,
                final_url=self._clean_text(final_url),
                content_type="text/html",
                html=html,
            )
            self._detect_blocked_content(html)
            enriched = self._parse_job_detail(html, job)
            logger.info("[%s] detalle leido correctamente: %s", self.portal_name, job.url)
            return enriched
        except (SourceBlockedError, CaptchaRequiredError) as exc:
            logger.warning("[%s] detalle omitido por error: %s (%s)", self.portal_name, job.url, exc)
            return job
        except LoginRequiredError as exc:
            logger.warning("[%s] detalle omitido por login/bloqueo: %s (%s)", self.portal_name, job.url, exc)
            return job
        except Exception as exc:
            logger.warning("[%s] detalle omitido por error: %s (%s)", self.portal_name, job.url, exc)
            return job
        finally:
            if owned_driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _load_rendered_html(self, driver, requested_url: str) -> tuple[str, str, int | None]:
        driver.set_page_load_timeout(self.settings.selenium_page_load_timeout)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                logger.info("[computrabajo] selenium_open_url=%s", requested_url)
                driver.get(requested_url)
                self._wait_for_rendered_dom(driver, COMPUTRABAJO_READY_SELECTORS)
                self._scroll_page(driver)
                self._wait_for_rendered_dom(driver, COMPUTRABAJO_READY_SELECTORS)
                html = getattr(driver, "page_source", "") or ""
                final_url = getattr(driver, "current_url", "") or requested_url
                logger.info("[computrabajo] page_loaded")
                logger.info("[computrabajo] html_length=%s", len(html))
                return html, final_url, 200 if html else None
            except Exception as exc:
                last_error = exc
                current_html = getattr(driver, "page_source", "") or ""
                current_url = getattr(driver, "current_url", "") or requested_url
                if self._is_timeout_error(exc) and attempt == 0:
                    logger.warning("[computrabajo] selenium_timeout retry=1 url=%s", requested_url)
                    continue
                if current_html and self.has_public_job_content(current_html):
                    logger.warning("[%s] selenium_partial_load_error=%s", self.portal_name, exc)
                    logger.info("[computrabajo] page_loaded")
                    logger.info("[computrabajo] html_length=%s", len(current_html))
                    return current_html, current_url, 200
                break
        raise RuntimeError(str(last_error) if last_error is not None else "selenium load failed")

    def _wait_for_rendered_dom(self, driver, selectors: tuple[str, ...]) -> None:
        wait_seconds = max(1, int(getattr(self.settings, "selenium_page_load_timeout", 1) or 1))
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait
        except ImportError:
            fallback_pause = max(0, self.settings.selenium_scroll_pause)
            if fallback_pause:
                sleep(fallback_pause)
            return

        for selector in selectors:
            try:
                WebDriverWait(driver, wait_seconds).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, selector))
                )
                return
            except Exception:
                continue

        fallback_pause = max(0, self.settings.selenium_scroll_pause)
        if fallback_pause:
            sleep(fallback_pause)

    def _handle_search_failure(
        self,
        requested_url: str,
        reason: str,
        *,
        final_url: str = "",
        html: str = "",
    ) -> str:
        resolved_url = final_url or requested_url
        self._record_search_snapshot(
            requested_url,
            resolved_url,
            html,
            status_code=None if not html else 200,
            block_reason=reason,
        )
        soup = BeautifulSoup(html, "html.parser") if html else BeautifulSoup("", "html.parser")
        job_cards = extract_job_cards_robust(html, soup) if html else []
        response = self._make_state_response(resolved_url, None if not html else 200)
        state = detect_blocking_state(response, html, job_cards)
        if state == COMPUTRABAJO_STATE_BLOCKED:
            self._log_search_state(state, response, html, job_cards)
            self._raise_blocked_search_state(response, html)
        effective_state = state
        if effective_state == COMPUTRABAJO_STATE_OK and not job_cards:
            effective_state = COMPUTRABAJO_STATE_SCRAPER_BROKEN
        self._log_search_state(effective_state, response, html, job_cards)
        logger.warning("[%s] selenium_failure=%s", self.portal_name, reason)
        return html if effective_state == COMPUTRABAJO_STATE_OK else ""

    def _raise_blocked_search_state(self, response, html: str) -> None:
        reason = _detect_blocking_reason(response, html)
        self._set_block_reason(reason)
        normalized_html = _normalize_computrabajo_text(html)
        if any(token in normalized_html for token in COMPUTRABAJO_CAPTCHA_TEXT_SIGNALS):
            raise CaptchaRequiredError(self.captcha_error_message)
        if any(token in normalized_html for token in COMPUTRABAJO_LOGIN_TEXT_SIGNALS):
            raise LoginRequiredError(self.login_error_message)
        final_url_path = _normalize_response_url_path(response)
        if any(token in final_url_path for token in COMPUTRABAJO_BLOCK_URL_SIGNALS):
            raise LoginRequiredError(self.login_error_message)
        raise SourceBlockedError(self.blocked_error_message)

    def _record_search_snapshot(
        self,
        requested_url: str,
        final_url: str,
        html: str,
        *,
        status_code: int | None,
        block_reason: str = "",
    ) -> None:
        self.last_response_debug = ResponseDebugSnapshot(
            requested_url=requested_url,
            status_code=status_code,
            final_url=self._clean_text(final_url),
            content_type="text/html",
            html=html,
            block_reason=block_reason,
        )

    def _make_state_response(self, final_url: str, status_code: int | None):
        return SimpleNamespace(status_code=status_code, url=final_url)

    def _is_timeout_error(self, exc: Exception) -> bool:
        message = str(exc).casefold()
        name = exc.__class__.__name__.casefold()
        return isinstance(exc, TimeoutError) or "timeout" in name or "timed out" in message or "timeout" in message

    def _parse_job_detail(self, html: str, job: ScrapedJob) -> ScrapedJob:
        soup = self._soup(html)
        structured = self._extract_jobposting_data(soup)
        title = self._first_non_empty(
            [structured.get("title", "")]
            + self._collect_texts(
                soup,
                (
                    "h1",
                    ".box_detail h1",
                    ".offer__title",
                    "[data-cy='job-title']",
                ),
            ),
            job.title,
        )
        company = self._first_non_empty(
            [structured.get("company", "")]
            + self._collect_texts(
                soup,
                (
                    ".box_company h2",
                    ".box_company a",
                    ".company",
                    "[data-cy='company-name']",
                ),
            ),
            job.company,
        )
        location = self._first_non_empty(
            [structured.get("location", "")]
            + self._collect_texts(
                soup,
                (
                    ".box_detail .fc_aux",
                    ".box_detail .mb5",
                    ".location",
                    "[data-cy='job-location']",
                ),
            ),
            job.location,
        )
        salary = structured.get("salary", "") or self._extract_salary(soup) or job.salary
        raw_posted_text = self._first_non_empty(
            [structured.get("raw_posted_text", "")]
            + self._collect_texts(
                soup,
                (
                    "time",
                    ".fc_aux.fs13",
                    ".box_detail .fc_aux",
                    "[data-cy='job-posted-date']",
                ),
            ),
            job.raw_posted_text,
        )
        description = self._build_description(soup, structured.get("description", "") or job.description)
        requirements = self._build_requirements(soup, description, job.requirements)
        modality = self._first_non_empty(
            [self._extract_modality_text(soup)],
            job.modality or self._infer_modality(location, description, requirements, structured.get("employment_type", "")),
        )

        return ScrapedJob(
            title=title,
            company=company,
            portal=job.portal,
            location=location,
            modality=modality,
            salary=salary,
            url=job.url,
            description=description,
            requirements=requirements,
            published_at=structured.get("published_at") or self._parse_published_at(raw_posted_text) or job.published_at,
            found_at=job.found_at,
            raw_posted_text=raw_posted_text,
            source_id=job.source_id,
        )

    def _build_description(self, soup, fallback: str) -> str:
        sections = self._collect_texts(
            soup,
            (
                ".box_detail .mbB",
                ".box_detail .text-description",
                ".box_detail [data-cy='job-description']",
                ".offer-description",
                ".description",
            ),
        )
        cleaned_sections = [text for text in sections if not self._looks_like_metadata(text)]
        return self._join_paragraphs(cleaned_sections) or fallback

    def _build_requirements(self, soup, description: str, fallback: str) -> str:
        sections = self._collect_texts(
            soup,
            (
                ".box_detail .mt20 ul li",
                ".box_detail .requirements li",
                ".requirements li",
                ".box_detail .requirements",
            ),
        )
        cleaned = [text for text in sections if text and text not in description]
        if cleaned:
            return self._join_paragraphs(cleaned)
        extracted = self._extract_requirements_from_description(description)
        if extracted:
            return extracted
        return fallback or description

    def _extract_modality_text(self, soup) -> str:
        candidates = self._collect_texts(
            soup,
            (
                ".box_detail .tag.base",
                ".box_detail .fc_aux",
                ".box_detail .mb5",
            ),
        )
        for text in candidates:
            modality = self._infer_modality(text)
            if modality:
                return modality
        return ""

    def _extract_salary(self, soup) -> str:
        candidates = self._collect_texts(
            soup,
            (
                ".box_detail .tag.base",
                ".salary",
                "[data-cy='job-salary']",
            ),
        )
        for text in candidates:
            cleaned = self._clean_detail_text(text)
            if re.search(r"[$]|cop|usd|\d", cleaned, re.IGNORECASE):
                return cleaned
        return ""

    def _extract_card_title(self, card: Tag) -> str:
        if card.name == "a":
            title = self._clean_text(card.get_text(" ", strip=True))
            if title:
                return title
        title = self._first_text(card, self.title_selectors)
        if title:
            return title
        anchor = _first_relevant_anchor(card)
        if anchor is None:
            return ""
        return self._clean_text(anchor.get_text(" ", strip=True))

    def _extract_card_url(self, card: Tag, source) -> str:
        anchor = _first_relevant_anchor(card)
        if anchor is None:
            return ""
        href = self._clean_text(str(anchor.get("href", "")))
        if not href:
            return ""
        return self._absolute_url(source, href)

    def _extract_card_company(self, card: Tag) -> str:
        company = self._first_text(card, self.company_selectors)
        if company:
            return company
        company = self._first_text(card, ("[class*='company']", "[class*='empresa']", ".it-company"))
        if company:
            return company
        title = self._extract_card_title(card)
        location = self._extract_card_location(card)
        posted = self._extract_card_posted_text(card)
        for node in card.find_all(["p", "span", "div", "small"]):
            text = self._clean_text(node.get_text(" ", strip=True))
            normalized = text.casefold()
            if not text or text in {title, location, posted}:
                continue
            if any(token in normalized for token in COMPUTRABAJO_COMPANY_HINTS):
                return text
        return ""

    def _extract_card_location(self, card: Tag) -> str:
        location = self._first_text(card, self.location_selectors)
        if location:
            return location
        location = self._first_text(card, ("[class*='location']", "[class*='ciudad']", ".it-location"))
        if location:
            return location
        title_normalized = self._normalize_text(self._extract_card_title(card))
        fallback = ""
        for tag_name in ("span", "p", "small", "div"):
            for node in card.find_all(tag_name):
                text = self._clean_text(node.get_text(" ", strip=True))
                normalized = text.casefold()
                if not text:
                    continue
                if title_normalized and title_normalized in normalized and normalized != title_normalized:
                    continue
                if not any(token in normalized for token in COMPUTRABAJO_LOCATION_HINTS):
                    continue
                if len(text.split()) <= 6:
                    return text
                if not fallback:
                    fallback = text
        if fallback:
            return fallback
        for node in card.find_all(["p", "span", "div", "small"]):
            text = self._clean_text(node.get_text(" ", strip=True))
            normalized = text.casefold()
            if text and any(token in normalized for token in COMPUTRABAJO_LOCATION_HINTS):
                return text
        return ""

    def _extract_card_posted_text(self, card: Tag) -> str:
        posted = self._first_text(card, self.posted_selectors)
        if posted:
            return posted
        for node in card.find_all(["span", "div", "p", "small", "time"]):
            text = self._clean_text(node.get_text(" ", strip=True))
            normalized = text.casefold()
            if text and any(token in normalized for token in COMPUTRABAJO_POSTED_HINTS):
                return text
        return ""

    def _extract_card_salary(self, card: Tag) -> str:
        salary = self._first_text(card, self.salary_selectors)
        if salary:
            return salary
        for node in card.find_all(["span", "div", "p", "small"]):
            text = self._clean_text(node.get_text(" ", strip=True))
            if text and re.search(r"[$]|cop|usd|\d", text, re.IGNORECASE):
                return text
        return ""

    def _extract_card_description(self, card: Tag) -> str:
        description = self._first_text(card, self.description_selectors)
        if description:
            return description
        description = self._first_text(card, (".summary", ".resumen", "[class*='description']"))
        if description:
            return description
        return ""

    def _extract_card_requirements(self, card: Tag) -> str:
        return self._first_text(card, ("[class*='requirements']", "[class*='requisitos']", ".requirements"))

    def _extract_jobposting_data(self, soup) -> dict[str, object]:
        for script in soup.select("script[type='application/ld+json']"):
            raw_text = script.get_text(strip=True)
            if not raw_text:
                continue
            for candidate in self._iterate_json_objects(raw_text):
                jobposting = self._find_jobposting(candidate)
                if not jobposting:
                    continue
                title = self._clean_detail_text(str(jobposting.get("title", "")))
                company = self._clean_detail_text(
                    str((jobposting.get("hiringOrganization") or {}).get("name", ""))
                )
                location = self._extract_structured_location(jobposting)
                description = self._html_to_text(str(jobposting.get("description", "")))
                salary = self._extract_structured_salary(jobposting)
                employment_type = self._clean_detail_text(str(jobposting.get("employmentType", "")))
                date_posted = self._clean_detail_text(str(jobposting.get("datePosted", "")))
                return {
                    "title": title,
                    "company": company,
                    "location": location,
                    "description": description,
                    "salary": salary,
                    "employment_type": employment_type,
                    "raw_posted_text": date_posted,
                    "published_at": self._parse_published_at(date_posted),
                }
        return {}

    def _iterate_json_objects(self, raw_text: str) -> list[object]:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
        return [parsed]

    def _find_jobposting(self, payload: object) -> dict | None:
        if isinstance(payload, dict):
            node_type = str(payload.get("@type", ""))
            if node_type == "JobPosting":
                return payload
            graph = payload.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    found = self._find_jobposting(item)
                    if found:
                        return found
        return None

    def _extract_structured_location(self, jobposting: dict) -> str:
        location = jobposting.get("jobLocation") or {}
        address = location.get("address") if isinstance(location, dict) else {}
        if not isinstance(address, dict):
            return ""
        parts = [
            self._clean_detail_text(str(address.get("addressLocality", ""))),
            self._clean_detail_text(str(address.get("addressRegion", ""))),
            self._clean_detail_text(str(address.get("addressCountry", ""))),
        ]
        unique_parts: list[str] = []
        seen: set[str] = set()
        for part in parts:
            if not part:
                continue
            normalized = part.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            unique_parts.append(part)
        return ", ".join(unique_parts)

    def _extract_structured_salary(self, jobposting: dict) -> str:
        salary = jobposting.get("baseSalary") or {}
        if not isinstance(salary, dict):
            return ""
        currency = self._clean_detail_text(str(salary.get("currency", jobposting.get("salaryCurrency", ""))))
        value = salary.get("value") or {}
        if isinstance(value, dict):
            amount = value.get("value", "")
        else:
            amount = value
        if amount in ("", None):
            return ""
        amount_text = self._clean_detail_text(str(amount))
        if currency:
            return f"{currency} {amount_text}"
        return amount_text

    def _html_to_text(self, value: str) -> str:
        if not value:
            return ""
        text = self._soup(value).get_text("\n", strip=True)
        return self._join_paragraphs(text.splitlines())

    def _extract_requirements_from_description(self, description: str) -> str:
        if not description:
            return ""
        match = re.search(r"\bRequisitos\b[:\s]*(.+)", description, re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        tail = match.group(1)
        tail = re.split(r"\bBeneficios\b", tail, maxsplit=1, flags=re.IGNORECASE)[0]
        parts = [
            self._clean_detail_text(piece)
            for piece in re.split(r"[\n\.]+", tail)
            if self._clean_detail_text(piece)
        ]
        return self._join_paragraphs(parts[:5])

    def _collect_texts(self, node, selectors: tuple[str, ...]) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for selector in selectors:
            for match in node.select(selector):
                text = self._clean_text(match.get_text(" ", strip=True))
                if not text or text in seen:
                    continue
                seen.add(text)
                values.append(text)
        return values

    def _join_paragraphs(self, values: list[str]) -> str:
        chunks: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = self._clean_detail_text(value)
            if not cleaned:
                continue
            normalized = cleaned.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            chunks.append(cleaned)
        return "\n".join(chunks)

    def _clean_detail_text(self, value: str) -> str:
        cleaned = self._clean_text(value)
        cleaned = re.sub(r"\s*â€¢\s*", " ", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned.strip(" -")

    def _first_non_empty(self, values: list[str], fallback: str) -> str:
        for value in values:
            cleaned = self._clean_detail_text(value)
            if cleaned:
                return cleaned
        return fallback

    def _log_search_state(self, state: str, response, html: str, job_cards) -> None:
        status_code = getattr(response, "status_code", None)
        cards_count = len(job_cards or [])
        html_length = len(html or "")
        message = (
            "[%s] estado=%s status=%s cards=%s html_len=%s final_url=%s"
        )
        final_url = getattr(response, "url", "") or ""
        if state == COMPUTRABAJO_STATE_BLOCKED:
            logger.warning(message, self.portal_name, state, status_code, cards_count, html_length, final_url)
            return
        if state == COMPUTRABAJO_STATE_SCRAPER_BROKEN:
            logger.warning(message, self.portal_name, state, status_code, cards_count, html_length, final_url)
            return
        if state == COMPUTRABAJO_STATE_NO_RESULTS:
            logger.info(message, self.portal_name, state, status_code, cards_count, html_length, final_url)
            return
        logger.info(message, self.portal_name, state, status_code, cards_count, html_length, final_url)

    def _looks_like_metadata(self, value: str) -> bool:
        normalized = self._normalize_text(value)
        if not normalized:
            return True
        return any(
            token in normalized
            for token in (
                "publicada",
                "hace ",
                "aplicar",
                "postularme",
                "guardar",
                "denunciar",
                "compartir",
                "ver mas",
                "ver menos",
                "candidatos",
            )
        )
