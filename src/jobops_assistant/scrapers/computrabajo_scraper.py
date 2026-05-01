from __future__ import annotations

import json
import logging
import re

from .base_scraper import (
    CaptchaRequiredError,
    ScrapedJob,
    SelectorBasedScraper,
    SourceBlockedError,
)


logger = logging.getLogger(__name__)


class ComputrabajoJobScraper(SelectorBasedScraper):
    portal_name = "computrabajo"
    card_selectors = ("article", "div.js-o-link", "div.box_offer")
    title_selectors = ("h2 a", "h2", ".title")
    company_selectors = (".fs16.fc_base.mt5", ".it-company", ".company")
    location_selectors = (".fs13.fc_aux.mt15", ".it-location", ".location")
    link_selectors = ("h2 a[href]", "a.js-o-link[href]", "a[href]")
    posted_selectors = (".fc_aux.fs13", ".it-posted", "time")
    description_selectors = (".mb10", ".description")
    salary_selectors = (".tag.base.mb10", ".salary", ".it-salary")

    def fetch_job_detail(self, job: ScrapedJob, source) -> ScrapedJob:
        try:
            html = self._request_text(job.url)
            enriched = self._parse_job_detail(html, job)
            logger.info("[%s] detalle leido correctamente: %s", self.portal_name, job.url)
            return enriched
        except (SourceBlockedError, CaptchaRequiredError) as exc:
            logger.warning("[%s] detalle omitido por error: %s (%s)", self.portal_name, job.url, exc)
            return job
        except Exception as exc:
            logger.warning("[%s] detalle omitido por error: %s (%s)", self.portal_name, job.url, exc)
            return job

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
            if re.search(r"[$€£]|cop|usd|\d", cleaned, re.IGNORECASE):
                return cleaned
        return ""

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
        cleaned = re.sub(r"\s*•\s*", " ", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned.strip(" -")

    def _first_non_empty(self, values: list[str], fallback: str) -> str:
        for value in values:
            cleaned = self._clean_detail_text(value)
            if cleaned:
                return cleaned
        return fallback

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
