from __future__ import annotations

import re

from .schemas import ParsedJobOffer


URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


def parse_offer_from_text(text: str) -> ParsedJobOffer:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    offer = ParsedJobOffer(description=text.strip())
    if lines:
        offer.title = lines[0]
    for line in lines[1:]:
        lower = line.lower()
        if lower.startswith("empresa:"):
            offer.company = line.split(":", 1)[1].strip()
        elif lower.startswith("portal:"):
            offer.portal = line.split(":", 1)[1].strip()
        elif lower.startswith("ubicacion:"):
            offer.location = line.split(":", 1)[1].strip()
        elif lower.startswith("modalidad:"):
            offer.modality = line.split(":", 1)[1].strip()
        elif lower.startswith("salario:"):
            offer.salary = line.split(":", 1)[1].strip()
    match = URL_PATTERN.search(text)
    if match:
        offer.url = match.group(0)
    return offer

