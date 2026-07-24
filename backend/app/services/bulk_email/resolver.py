"""Find an email address for someone the user pasted without one.

Two stages, because neither alone is enough. A pasted roster line like
"Dr. Alex Tatum — Private-practice urologist, Indianapolis" names a real person
but gives no identifier any data provider can match on, so Claude web-searches
first to turn the description into an employer, a domain and ideally a LinkedIn
URL. Apollo is then asked for the address using the strongest identifier that
search produced.

Nothing here writes to a contact. Every result is a proposal carrying its own
evidence, and the user accepts or rejects it in the review queue.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from app.services.bulk_email.llm import llm_available, research_json
from app.services.enrichment import get_enrichment_provider
from app.services.enrichment.base import EnrichmentContact

logger = logging.getLogger(__name__)

# Below this the match is treated as too shaky to spend an Apollo credit on.
MIN_CONFIDENCE = 0.5
# Apollo matches up to ten people per call.
_APOLLO_BATCH = 10
_HONORIFIC_RE = re.compile(
    r"^\s*(dr|doctor|prof|professor|mr|mrs|ms|miss|sir|rev|hon)\.?\s+", re.IGNORECASE
)
_SUFFIX_RE = re.compile(
    r"[,\s]+(md|do|phd|pharmd|dds|dvm|rn|np|pa|esq|jr|sr|ii|iii|iv)\.?$",
    re.IGNORECASE,
)

_IDENTIFY_SYSTEM = (
    "You identify a specific real person from a short description someone wrote "
    "down at an event, so their work email can be looked up in a contact "
    "database. Use web search.\n\n"
    "Return ONLY a JSON object with these keys:\n"
    '  "found": true only if you are confident you identified this exact person,\n'
    '  "full_name": their correctly spelled full name,\n'
    '  "title": their current job title,\n'
    '  "organization": their current employer,\n'
    '  "domain": the employer\'s email domain (e.g. "acme.com"), no www or @,\n'
    '  "linkedin_url": their personal LinkedIn profile URL,\n'
    '  "location": city and region,\n'
    '  "confidence": 0 to 1, how sure you are this is the same person,\n'
    '  "ambiguous": true if several different real people fit the description,\n'
    '  "reason": one short sentence on how you concluded this, or what blocked you.\n'
    "Use null for anything you could not establish.\n\n"
    "HARD RULES:\n"
    "- Never guess or construct an email address. That is not your job here.\n"
    "- The description may be a rough transcript: spellings can be wrong and the "
    "name may be given with alternatives. Correct an obvious misspelling only "
    "when the rest of the description clearly pins down one person.\n"
    "- If the description fits several people, or you cannot tell which one it "
    'is, set "ambiguous": true and "found": false. Say so rather than picking.\n'
    "- Only report a domain or LinkedIn URL you actually saw in a source.\n"
    "- A person's employer must be their CURRENT one."
)


@dataclass
class Identity:
    """What the web search concluded about one pasted person."""

    found: bool = False
    full_name: Optional[str] = None
    title: Optional[str] = None
    organization: Optional[str] = None
    domain: Optional[str] = None
    linkedin_url: Optional[str] = None
    location: Optional[str] = None
    confidence: float = 0.0
    ambiguous: bool = False
    reason: Optional[str] = None
    sources: list[dict] = field(default_factory=list)


@dataclass
class PersonQuery:
    """The pasted person we are trying to place, as stored on their lookup row."""

    lookup_id: int
    name: str
    title: Optional[str] = None
    company: Optional[str] = None
    source_text: Optional[str] = None


def unsearchable_reason(person: PersonQuery) -> Optional[str]:
    """Why this person cannot be searched for at all, if that is the case.

    A lone first name with nothing attached ("Steve", "Elizabeth") matches
    thousands of people, so it is refused before a search is paid for.
    """
    words = [w for w in re.split(r"[^A-Za-z]+", person.name or "") if len(w) > 1]
    if not words:
        return "No name was given for this person."
    if len(words) < 2 and not (person.company or "").strip():
        return "Only a first name was given, with no organization to place them."
    return None


def identify(person: PersonQuery) -> Identity:
    """Web-search who this person is. Never raises; a failure is a low-confidence miss."""
    if not llm_available():
        return Identity(reason="No Anthropic API key configured, so no web search ran.")

    known = [f"Name as written: {person.name}"]
    if person.title:
        known.append(f"Role as written: {person.title}")
    if person.company:
        known.append(f"Organization as written: {person.company}")
    if person.source_text and person.source_text.strip() != person.name:
        known.append(f"The full line they were listed on: {person.source_text}")

    try:
        data, sources = research_json(
            _IDENTIFY_SYSTEM,
            "Identify this person and find their current employer:\n\n"
            + "\n".join(known),
            max_tokens=1024,
        )
    except Exception as exc:  # noqa: BLE001 - one bad lookup must not stop the batch
        logger.warning("Identity lookup failed for %s: %s", person.name, exc)
        return Identity(reason=f"Lookup failed: {exc}")

    if not isinstance(data, dict):
        return Identity(reason="The search came back empty.", sources=sources)

    identity = Identity(
        found=bool(data.get("found")),
        full_name=_text(data.get("full_name")) or person.name,
        title=_text(data.get("title")),
        organization=_text(data.get("organization")),
        domain=_domain(data.get("domain")),
        linkedin_url=_linkedin(data.get("linkedin_url")),
        location=_text(data.get("location")),
        confidence=_confidence(data.get("confidence")),
        ambiguous=bool(data.get("ambiguous")),
        reason=_text(data.get("reason")),
        sources=sources,
    )
    if identity.ambiguous:
        identity.found = False
    return identity


def find_emails(queries: list[tuple[PersonQuery, Identity]]) -> dict[int, EnrichmentContact]:
    """Ask Apollo for addresses, keyed by lookup id.

    Only people with an identifier Apollo can match on are sent, so credits are
    not spent on rows the search could not place.
    """
    matchable = [
        (query, identity)
        for query, identity in queries
        if _apollo_ready(query, identity)
    ]
    if not matchable:
        return {}

    provider = get_enrichment_provider()
    results: dict[int, EnrichmentContact] = {}
    for start in range(0, len(matchable), _APOLLO_BATCH):
        batch = matchable[start : start + _APOLLO_BATCH]
        contacts = [_enrichment_contact(q, i) for q, i in batch]
        try:
            provider.reveal_contacts(contacts)
        except Exception as exc:  # noqa: BLE001 - keep the remaining batches going
            logger.warning("Apollo reveal failed for a bulk lookup batch: %s", exc)
            continue
        for (query, _identity), contact in zip(batch, contacts):
            results[query.lookup_id] = contact
    return results


def _apollo_ready(query: PersonQuery, identity: Identity) -> bool:
    """True when we hold an identifier worth spending a credit on.

    Apollo matches on a LinkedIn URL, or on a name paired with an employer. A
    bare name matches almost nothing and burns a call, so it is skipped.
    """
    if identity.ambiguous or identity.confidence < MIN_CONFIDENCE:
        return False
    if identity.linkedin_url:
        return True
    has_employer = bool(identity.domain or identity.organization or query.company)
    return bool(identity.full_name or query.name) and has_employer


def _enrichment_contact(query: PersonQuery, identity: Identity) -> EnrichmentContact:
    return EnrichmentContact(
        name=_plain_name(identity.full_name or query.name),
        title=identity.title or query.title,
        linkedin_url=identity.linkedin_url,
        domain=identity.domain,
        organization_name=identity.organization or query.company,
        location=identity.location,
    )


def _plain_name(name: str) -> str:
    """Drop honorifics and suffixes, which only weaken a database match."""
    stripped = _HONORIFIC_RE.sub("", name or "").strip()
    stripped = _SUFFIX_RE.sub("", stripped).strip(" ,")
    return stripped or name


def _text(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text or text.lower() in ("none", "null", "n/a", "unknown", "-"):
        return None
    return text


def _domain(value: object) -> Optional[str]:
    text = (_text(value) or "").lower()
    text = re.sub(r"^https?://", "", text).removeprefix("www.").split("/")[0]
    text = text.lstrip("@").strip()
    return text if "." in text and " " not in text else None


def _linkedin(value: object) -> Optional[str]:
    text = _text(value)
    if not text or "linkedin.com/in/" not in text.lower():
        return None
    return text


def _confidence(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    # Tolerate a model answering on a 0-100 scale.
    if number > 1:
        number = number / 100
    return max(0.0, min(1.0, number))
