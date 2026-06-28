from __future__ import annotations

from urllib.parse import parse_qsl, quote, urlparse


LINKEDIN_SEARCH_BASE_URL = "https://www.linkedin.com/jobs/search/"
LINKEDIN_SOURCE_PORTALS = {"linkedin", "linkedin_selenium"}
LINKEDIN_REMOVED_QUERY_PARAMS = {"currentjobid", "origin"}
LINKEDIN_ALWAYS_ON_PARAMS = {"f_al": "true"}


def is_linkedin_source_portal(portal: str) -> bool:
    return (portal or "").strip().lower() in LINKEDIN_SOURCE_PORTALS


def is_linkedin_jobs_search_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    hostname = (parsed.netloc or "").casefold()
    normalized_path = (parsed.path or "").rstrip("/") or "/"
    return hostname.endswith("linkedin.com") and normalized_path.startswith("/jobs/search")


def build_linkedin_url(base_url: str, keywords: str, location: str, time_filter: str) -> str:
    cleaned_url = (base_url or "").strip()
    parsed = urlparse(cleaned_url or LINKEDIN_SEARCH_BASE_URL)

    existing_values: dict[str, str] = {}
    existing_keys: dict[str, str] = {}
    ordered_existing_keys: list[str] = []
    removed_params: list[str] = []

    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.casefold()
        cleaned_value = (value or "").strip()
        if normalized_key in LINKEDIN_REMOVED_QUERY_PARAMS:
            removed_params.append(key)
            continue
        if normalized_key in LINKEDIN_ALWAYS_ON_PARAMS:
            expected_value = LINKEDIN_ALWAYS_ON_PARAMS[normalized_key]
            if cleaned_value.casefold() != expected_value:
                removed_params.append(f"{key}={cleaned_value}" if cleaned_value else key)
            continue
        if normalized_key == "sortby":
            if cleaned_value.casefold() != "dd":
                removed_params.append(f"{key}={cleaned_value}" if cleaned_value else key)
            continue
        if normalized_key in existing_values:
            continue
        existing_values[normalized_key] = cleaned_value
        existing_keys[normalized_key] = key
        ordered_existing_keys.append(normalized_key)

    effective_keywords = (keywords or "").strip() or existing_values.get("keywords", "")
    effective_location = (location or "").strip() or existing_values.get("location", "")
    effective_time_filter = (time_filter or "").strip() or existing_values.get("f_tpr", "")

    params: list[tuple[str, str]] = []
    if effective_keywords:
        params.append(("keywords", effective_keywords))
    if effective_location:
        params.append(("location", effective_location))
    if effective_time_filter:
        params.append(("f_TPR", effective_time_filter))

    for normalized_key, canonical_key in (("f_e", "f_E"), ("f_wt", "f_WT"), ("geoid", "geoId")):
        value = existing_values.get(normalized_key, "")
        if value:
            params.append((canonical_key, value))
    params.append(("f_AL", "true"))

    consumed_keys = {
        "keywords",
        "location",
        "f_tpr",
        "f_e",
        "f_wt",
        "geoid",
        *LINKEDIN_ALWAYS_ON_PARAMS,
        "sortby",
        *LINKEDIN_REMOVED_QUERY_PARAMS,
    }
    for normalized_key in ordered_existing_keys:
        if normalized_key in consumed_keys:
            continue
        value = existing_values.get(normalized_key, "")
        if not value:
            continue
        params.append((existing_keys.get(normalized_key, normalized_key), value))

    params.append(("sortBy", "DD"))
    query = "&".join(f"{quote(key)}={quote(value, safe=',')}" for key, value in params)
    normalized_url = f"{LINKEDIN_SEARCH_BASE_URL}?{query}"

    if cleaned_url != normalized_url:
        original_display = cleaned_url or LINKEDIN_SEARCH_BASE_URL
        removed_display = ", ".join(removed_params) if removed_params else "ninguno"
        print(f"LinkedIn URL original: {original_display}")
        print(f"LinkedIn URL limpia: {normalized_url}")
        print(f"LinkedIn URL params eliminados: {removed_display}")

    return normalized_url


def normalize_linkedin_source_url(
    portal: str,
    search_url: str,
    *,
    keywords: str = "",
    location: str = "",
    time_filter: str = "",
) -> str:
    cleaned_url = (search_url or "").strip()
    if not is_linkedin_source_portal(portal):
        return cleaned_url
    if not cleaned_url:
        if not any((keywords.strip(), location.strip(), time_filter.strip())):
            return cleaned_url
        return build_linkedin_url(LINKEDIN_SEARCH_BASE_URL, keywords, location, time_filter)
    if not is_linkedin_jobs_search_url(cleaned_url):
        return cleaned_url
    return build_linkedin_url(cleaned_url, keywords, location, time_filter)
