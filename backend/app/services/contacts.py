"""Prospect (people) discovery, ranking, and role classification.

Ranks people by how useful they are as a relationship/decision-maker for a
principal: investors, operating partners, board members, CEOs, founders, and
senior executives rank highest. All discovered prospects start unapproved.
"""
from __future__ import annotations

import re
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.config import resolve_apollo_phone_webhook_url, settings
from app.models.company import Company
from app.models.contact import Contact
from app.models.enums import (
    AuditAction,
    EnrichmentStatus,
    PhoneRevealStatus,
    RoleCategory,
)
from app.models.principal import Principal
from app.services.audit import log_action
from app.services.enrichment import get_enrichment_provider
from app.services.enrichment.base import DiscoveryCriteria, EnrichmentContact


class RevealNotAllowed(Exception):
    """Raised when a credit-consuming reveal is blocked by the board-fit gate."""


class ApprovalBlocked(Exception):
    """Raised when a contact still isn't outreach-ready after best-effort
    auto-research/reveal (see approve_contact)."""

# Target titles with a base usefulness weight (0-100). Higher = better entry point.
TARGET_TITLES = {
    "chief executive officer": 95,
    "ceo": 95,
    "founder": 92,
    "co-founder": 92,
    "managing partner": 92,
    "general partner": 90,
    "operating partner": 90,
    "managing director": 88,
    "board member": 90,
    "board director": 90,
    "chairman": 90,
    "chairwoman": 90,
    "president": 86,
    "chief operating officer": 85,
    "coo": 85,
    "chief financial officer": 82,
    "cfo": 82,
    "chief medical officer": 84,
    "cmo": 80,
    "partner": 84,
    "principal": 78,
    "head of": 74,
    "vice president": 72,
    "vp": 72,
    "director": 66,
    "machine learning engineer": 88,
    "software engineer": 82,
    "data scientist": 84,
    "ai engineer": 86,
    "staff engineer": 80,
    "principal engineer": 90,
}

# Map title keywords to a role category (order matters: most specific first).
# Board-access personas are listed before generic operator/investor roles so a
# "Talent Partner" is not swallowed by the broad "partner" investor match.
_ROLE_KEYWORDS = [
    # --- Tier 1: board-access gatekeepers ---
    (RoleCategory.BOARD_SEARCH_CONSULTANT, (
        "board search", "board practice", "board & ceo", "ceo & board",
        "director search", "board advisory", "leadership advisory",
        "board recruitment", "executive search",
    )),
    (RoleCategory.TALENT_PARTNER, (
        "talent partner", "head of talent", "human capital", "people partner",
        "portfolio talent", "head of human capital", "talent advisor",
    )),
    (RoleCategory.OPERATING_PARTNER, (
        "operating partner", "value creation", "portfolio operations",
        "operating advisor", "operating executive",
    )),
    # --- Tier 2: board peers / sponsors ---
    (RoleCategory.AUDIT_COMMITTEE, ("audit committee", "audit chair", "chair of audit", "chair, audit")),
    (RoleCategory.GOVERNANCE, ("nominating", "governance committee", "chair of governance", "governance chair")),
    (RoleCategory.INDEPENDENT_DIRECTOR, (
        "independent director", "non-executive director", "non executive director",
        "lead director", "lead independent",
    )),
    (RoleCategory.BOARD_MEMBER, (
        "board member", "board director", "board chair", "chair of the board",
        "chairman", "chairwoman", "chairperson", "non-exec",
    )),
    # --- Tier 3: operators & investors ---
    (RoleCategory.INVESTOR, ("general partner", "managing partner", "venture partner", "investor", "partner")),
    (RoleCategory.FOUNDER, ("founder", "co-founder", "cofounder")),
    (RoleCategory.CEO, ("chief executive", "ceo")),
    (RoleCategory.ENGINEER, (
        "machine learning", "ml engineer", "software engineer", "ai engineer",
        "data scientist", "research scientist", "staff engineer", "principal engineer",
        "engineering manager", "tech lead", "developer", "programmer",
    )),
    (RoleCategory.EXECUTIVE, ("chief", "coo", "cfo", "cmo", "cto", "president", "vice president", "vp", "head of")),
    (RoleCategory.DECISION_MAKER, ("director", "principal", "managing director")),
]

# Board-fit tiers: how directly a persona influences independent-director
# appointments for the principal. Tier 1 are gatekeepers (search firms + PE
# talent/operating partners), Tier 2 are board peers/sponsors, Tier 3 are
# operators/investors who can refer. Drives who we spend reveal credits on.
_BOARD_FIT_TIERS = {
    RoleCategory.BOARD_SEARCH_CONSULTANT: (1, 94.0),
    RoleCategory.TALENT_PARTNER: (1, 92.0),
    RoleCategory.OPERATING_PARTNER: (1, 90.0),
    RoleCategory.AUDIT_COMMITTEE: (2, 86.0),
    RoleCategory.INDEPENDENT_DIRECTOR: (2, 82.0),
    RoleCategory.GOVERNANCE: (2, 80.0),
    RoleCategory.BOARD_MEMBER: (2, 78.0),
    RoleCategory.INVESTOR: (3, 66.0),
    RoleCategory.CEO: (3, 64.0),
    RoleCategory.FOUNDER: (3, 60.0),
    RoleCategory.EXECUTIVE: (4, 50.0),
    RoleCategory.DECISION_MAKER: (4, 48.0),
    RoleCategory.OTHER: (5, 30.0),
}

# Healthcare / pharma / PE / regulated signals that strengthen fit for Dalbir's
# vertical. Matched against title + employer industry text.
_VERTICAL_SIGNALS = (
    "health", "healthcare", "pharma", "pharmaceutical", "biotech", "life science",
    "medical", "clinical", "hospital", "care", "private equity", "buyout",
    "family office", "503b", "cgmp", "fda", "regulated",
)

# Apollo-style seniority guesses from title keywords (when not provided).
_SENIORITY_KEYWORDS = [
    ("owner", ("founder", "co-founder", "owner")),
    ("c_suite", ("chief", "ceo", "coo", "cfo", "cmo", "cto", "president")),
    ("partner", ("partner",)),
    ("board_member", ("board",)),
    ("vp", ("vice president", "vp")),
    ("head", ("head of",)),
    ("director", ("director",)),
]


def classify_role_category(title: Optional[str]) -> str:
    """Best-effort decision-maker category from a title string."""
    if not title:
        return RoleCategory.OTHER
    title_l = title.lower()
    for category, keywords in _ROLE_KEYWORDS:
        if any(k in title_l for k in keywords):
            return category
    return RoleCategory.OTHER


def infer_seniority(title: Optional[str]) -> Optional[str]:
    """Best-effort Apollo-style seniority from a title string."""
    if not title:
        return None
    title_l = title.lower()
    for seniority, keywords in _SENIORITY_KEYWORDS:
        if any(k in title_l for k in keywords):
            return seniority
    return None


def score_contact_title(title: Optional[str], employee_count: Optional[int]) -> tuple[float, str]:
    """Return (usefulness_score, reason) for a contact title.

    ``employee_count`` is accepted for signature compatibility; for relationship
    sourcing seniority of the person matters more than company size.
    """
    if not title:
        return 40.0, "No title available; default mid-low usefulness."
    title_l = title.lower()

    base = 45.0
    matched_key = None
    for key, weight in TARGET_TITLES.items():
        if key in title_l and weight > base:
            base = float(weight)
            matched_key = key

    if matched_key:
        reason = f"matches senior decision-maker role '{matched_key}'."
    else:
        reason = "title not a recognized senior decision-maker role."

    return min(100.0, base), reason


def board_fit_score(
    title: Optional[str],
    *,
    role_category: Optional[str] = None,
    industry: Optional[str] = None,
    has_board_job_signal: bool = False,
) -> tuple[float, int, str]:
    """Score how relevant a person is to board-seat sourcing (0-100) + tier + reason.

    Board appointments flow through a few channels: board-search consultants, PE
    talent/operating partners (Tier 1), sitting directors and audit/governance
    chairs (Tier 2), and operators/investors who can refer (Tier 3). This score
    ranks prospects so we research and spend reveal credits on the right people.
    """
    category = role_category or classify_role_category(title)
    tier, base = _BOARD_FIT_TIERS.get(category, (5, 30.0))

    text = f"{title or ''} {industry or ''}".lower()
    vertical_hit = any(sig in text for sig in _VERTICAL_SIGNALS)
    # Reward the principal's vertical (healthcare / pharma / PE / regulated).
    bonus = 6.0 if vertical_hit else 0.0
    # Audit-committee / financial-expert angle is Dalbir's strongest wedge.
    if any(k in text for k in ("audit", "cfo", "finance", "financial")):
        bonus += 4.0
    if has_board_job_signal:
        bonus += 15.0

    score = min(100.0, base + bonus)
    label = {1: "Tier 1 gatekeeper", 2: "Tier 2 board peer", 3: "Tier 3 referrer"}.get(
        tier, "off-target"
    )
    reason = f"{label}: {category.replace('_', ' ')}"
    if vertical_hit:
        reason += "; healthcare/PE vertical match"
    if has_board_job_signal:
        reason += "; employer has active board/director job posting"
    return score, tier, reason


def discovery_fit_score(
    title: Optional[str],
    criteria: Optional[DiscoveryCriteria] = None,
    *,
    industry: Optional[str] = None,
) -> tuple[float, str]:
    """Score how well a prospect matches the active discovery search (0-100).

    Unlike board_fit_score, this reflects the user's ICP titles/industries —
    e.g. ML engineers rank highly for an AI-engineer search, not for board sourcing.
    """
    if not title:
        return 55.0, "No title; neutral match to search criteria."

    title_l = title.lower()
    best = 0.0
    reason = "Included from discovery search."

    search_titles = list(criteria.titles) if criteria and criteria.titles else []
    search_text = " ".join(search_titles).lower()
    is_tech_search = any(
        w in search_text
        for w in (
            "engineer",
            "machine learning",
            "software",
            "developer",
            "scientist",
            " ai",
            "ml ",
            "data ",
        )
    )

    for target in search_titles:
        tl = target.lower()
        if tl in title_l or title_l in tl:
            best = max(best, 94.0)
            reason = f"Title matches search target '{target}'."
        else:
            target_words = [w for w in re.split(r"[\W_]+", tl) if len(w) > 3]
            overlap = sum(1 for w in target_words if w in title_l)
            if overlap >= 2:
                best = max(best, 86.0)
                reason = f"Title closely overlaps search target '{target}'."
            elif overlap == 1:
                best = max(best, 74.0)
                reason = f"Title partially matches search target '{target}'."

    if is_tech_search and any(
        w in title_l
        for w in ("engineer", "scientist", "developer", "machine learning", "software", "research")
    ):
        best = max(best, 82.0)
        if reason == "Included from discovery search.":
            reason = "Technical role matches engineering search criteria."

    if best == 0.0:
        generic, gen_reason = score_contact_title(title, None)
        best = generic
        reason = gen_reason

    if criteria and criteria.industries and industry:
        ind_l = industry.lower()
        for tag in criteria.industries:
            if tag.lower() in ind_l or ind_l in tag.lower():
                best = min(100.0, best + 4.0)
                reason += "; employer industry matches search."
                break

    return min(100.0, best), reason


def reveal_eligible(contact: Contact) -> tuple[bool, float, str]:
    """Whether a credit-consuming Apollo reveal is allowed.

    Manual reveals (Prospects page) are always allowed — clicking Reveal *is* the
    human credit gate. Automated agent flows pre-filter via research scores before
    calling reveal. Numeric thresholds are not used to block user-initiated reveals.
    """
    fit = contact.usefulness_score
    if fit is None and contact.relevance_score is not None:
        fit = contact.relevance_score
    fit = fit or 0.0
    if contact.do_not_contact:
        return False, fit, "marked do not contact"
    return True, fit, "user-initiated reveal"


def ensure_discovery_fit(
    db: Session,
    contact: Contact,
    *,
    criteria: Optional[DiscoveryCriteria] = None,
) -> None:
    """Backfill usefulness_score from discovery criteria when missing."""
    if contact.usefulness_score is not None:
        return
    if criteria is None and contact.discovery_run_id:
        from app.models.discovery_run import DiscoveryRun

        run = db.get(DiscoveryRun, contact.discovery_run_id)
        if run and run.criteria:
            c = run.criteria
            criteria = DiscoveryCriteria(
                industries=c.get("industries"),
                company_types=c.get("company_types"),
                healthcare_sectors=c.get("healthcare_sectors"),
                geographies=c.get("geographies"),
                titles=c.get("titles"),
                seniorities=c.get("seniorities"),
                keywords=c.get("keywords"),
                themes=c.get("themes"),
            )
    if criteria is None:
        return
    company = getattr(contact, "company", None)
    industry = company.industry if company else None
    score, reason = discovery_fit_score(contact.title, criteria, industry=industry)
    contact.usefulness_score = score
    contact.rank_reason = reason
    db.flush()


def enrich_and_find_contacts(
    db: Session, company: Company, *, max_contacts: Optional[int] = None
) -> List[Contact]:
    """Enrich a company and create ranked contact rows. Returns newly created rows.

    When ``max_contacts`` is set (from the job page), it controls how many people
    Apollo searches for, how many are saved, and how many get email/phone reveal.
    When omitted (Companies page), defaults from settings apply.
    """
    provider = get_enrichment_provider()

    fetch_limit = (
        max(1, min(max_contacts, 25))
        if max_contacts is not None
        else settings.apollo_contacts_per_company
    )
    reveal_limit = (
        max(1, min(max_contacts, 25))
        if max_contacts is not None
        else settings.apollo_enrich_contacts_limit
    )
    save_limit = fetch_limit if max_contacts is not None else None

    company.enrichment_status = EnrichmentStatus.IN_PROGRESS
    db.flush()

    result = provider.enrich_company(
        company.name, linkedin_url=company.linkedin_url, domain=company.domain
    )
    if result.found:
        company.domain = company.domain or result.domain
        company.website = company.website or result.website
        company.linkedin_url = company.linkedin_url or result.linkedin_url
        company.industry = company.industry or result.industry
        company.employee_count = company.employee_count or result.employee_count
        company.headquarters = company.headquarters or result.headquarters
        company.phone = company.phone or result.phone
        company.funding = company.funding or result.funding
        company.revenue = company.revenue or result.revenue
        company.enrichment_status = EnrichmentStatus.ENRICHED
        company.enrichment_source = result.source
    else:
        company.enrichment_status = EnrichmentStatus.FAILED

    found_contacts = provider.find_contacts(
        company.name,
        domain=company.domain,
        target_titles=list(TARGET_TITLES),
        limit=fetch_limit if max_contacts is not None else None,
    )

    existing_ids = {c.external_id for c in company.contacts if c.external_id}
    existing_keys = {
        (c.name or "").strip().lower() + "|" + (c.title or "").strip().lower()
        for c in company.contacts
    }

    scored: List[tuple] = []
    for ec in found_contacts:
        # Prefer the stable Apollo person id for dedup; names can be obfuscated
        # (e.g. "Wiley Bl***p") in search results and only resolved on reveal.
        if ec.external_id and ec.external_id in existing_ids:
            continue
        key = (ec.name or "").strip().lower() + "|" + (ec.title or "").strip().lower()
        if not ec.external_id and key in existing_keys:
            continue
        if ec.external_id:
            existing_ids.add(ec.external_id)
        existing_keys.add(key)
        usefulness, reason = score_contact_title(ec.title, company.employee_count)
        scored.append((usefulness, reason, ec))

    scored.sort(key=lambda t: t[0], reverse=True)
    if save_limit is not None:
        scored = scored[:save_limit]

    phone_reveal_enabled = bool(
        settings.apollo_reveal_phone_number and resolve_apollo_phone_webhook_url()
    )

    revealed_count = 0
    phone_pending_count = 0
    top_ids: set[str] = set()
    if settings.apollo_reveal_contacts and scored:
        top = [ec for _, _, ec in scored]
        top_ids = {ec.external_id for ec in top if ec.external_id}
        if top:
            provider.reveal_contacts(top)
            revealed_count = sum(1 for ec in top if ec.email)

    created: List[Contact] = []
    for usefulness, reason, ec in scored:
        phone_status = None
        if phone_reveal_enabled and ec.external_id and ec.external_id in top_ids:
            if ec.phone:
                phone_status = PhoneRevealStatus.REVEALED
            else:
                phone_status = PhoneRevealStatus.PENDING
                phone_pending_count += 1

        contact = Contact(
            company_id=company.id,
            name=ec.name,
            title=ec.title,
            role_category=classify_role_category(ec.title),
            seniority=infer_seniority(ec.title),
            email=ec.email,
            email_status=ec.email_status,
            phone=ec.phone,
            phone_reveal_status=phone_status,
            linkedin_url=ec.linkedin_url,
            external_id=ec.external_id,
            source=result.source,
            confidence_score=ec.confidence_score,
            usefulness_score=usefulness,
            rank_reason=reason,
        )
        db.add(contact)
        created.append(contact)

    db.flush()

    # Reveal emails for existing top contacts (e.g. prior run saved 10, user now asks for 2).
    extra_revealed, extra_phones = _reveal_top_company_contacts(
        db, company, provider, reveal_limit, phone_reveal_enabled
    )
    revealed_count += extra_revealed
    phone_pending_count += extra_phones

    log_action(
        db,
        AuditAction.ENRICHMENT,
        entity_type="company",
        entity_id=company.id,
        summary=(
            f"Enriched via {result.source}; created {len(created)} contact(s), "
            f"revealed {revealed_count} email(s), {phone_pending_count} phone(s) pending."
        ),
        detail={
            "enrichment_source": result.source,
            "contacts": len(created),
            "emails_revealed": revealed_count,
            "phones_pending": phone_pending_count,
            "max_contacts": max_contacts,
        },
    )
    return created


def reveal_contact(db: Session, contact: Contact) -> Contact:
    """Reveal a single prospect's email, phone, real name, and LinkedIn via Apollo.

    Apollo's People Search obfuscates last names (e.g. "Nicole An***e") and never
    returns emails. Resolving them requires a separate bulk_match call that
    consumes credits, so this runs only on demand for one prospect at a time.
    """
    # Credit gate: spend reveals on approved prospects, discovery matches, or high scores.
    ensure_discovery_fit(db, contact)
    eligible, fit, _reason = reveal_eligible(contact)
    if not eligible:
        raise RevealNotAllowed(
            f"Reveal blocked: this prospect is marked do not contact."
        )

    provider = get_enrichment_provider()
    if not contact.external_id and not contact.linkedin_url:
        return contact

    company = getattr(contact, "company", None)
    domain = company.domain if company else None

    ec = EnrichmentContact(
        name=contact.name,
        title=contact.title,
        email=contact.email,
        external_id=contact.external_id,
        domain=domain,
        linkedin_url=contact.linkedin_url,
    )
    provider.reveal_contacts([ec])

    phone_reveal_enabled = bool(
        settings.apollo_reveal_phone_number and resolve_apollo_phone_webhook_url()
    )

    if ec.email and not contact.email:
        contact.email = ec.email
        contact.email_status = ec.email_status
    elif ec.email_status and not contact.email_status:
        contact.email_status = ec.email_status
    if ec.phone:
        contact.phone = ec.phone
        contact.phone_reveal_status = PhoneRevealStatus.REVEALED
    elif phone_reveal_enabled and contact.external_id and not contact.phone:
        contact.phone_reveal_status = PhoneRevealStatus.PENDING
    if ec.name:
        contact.name = ec.name
    if ec.linkedin_url:
        contact.linkedin_url = ec.linkedin_url
    if ec.location:
        contact.location = ec.location

    db.flush()
    log_action(
        db,
        AuditAction.ENRICHMENT,
        entity_type="contact",
        entity_id=contact.id,
        summary=(
            f"Revealed contact details for {contact.name} "
            f"(email={'yes' if contact.email else 'no'}, "
            f"phone={'yes' if contact.phone else 'pending/no'})."
        ),
    )
    return contact


def approve_contact(
    db: Session, contact: Contact, principal: Principal, *, approved_by: str = "user"
) -> None:
    """Approve one contact for outreach, best-effort auto-research/reveal first.

    Shared by the single-prospect approval endpoint (one HTTP request) and the
    run-level bulk-approve background job (many of these fanned out across a
    thread pool) so both apply identical rules. Raises RevealNotAllowed or
    ApprovalBlocked if the contact still isn't ready afterward; caller decides
    how to surface that (HTTP 400 for one, a per-contact failure note for many).
    Commits its own work.
    """
    from app.services.insights.engine import generate_insight
    from app.services.linkedin_providers import public_identifier_from_url
    from app.services.outreach_eligibility import outreach_draft_blockers
    from app.services.outreach_goal import outreach_goal_for_contact

    blockers = outreach_draft_blockers(db, principal_id=principal.id, contact=contact)
    if "email not revealed" in blockers:
        try:
            ensure_discovery_fit(db, contact)
            reveal_contact(db, contact)
            db.commit()
        except RevealNotAllowed:
            db.rollback()
            raise
        except Exception:  # noqa: BLE001 - best effort; re-checked below
            db.rollback()
    if "not researched" in blockers or "research failed — retry research first" in blockers:
        company = db.get(Company, contact.company_id) if contact.company_id else None
        try:
            generate_insight(
                db,
                principal,
                contact=contact,
                company=company,
                outreach_goal=outreach_goal_for_contact(db, contact, principal),
            )
            db.commit()
        except Exception:  # noqa: BLE001 - best effort; re-checked below
            db.rollback()

    blockers = outreach_draft_blockers(db, principal_id=principal.id, contact=contact)
    # A prospect reachable on LinkedIn (a personal /in/ profile) can be
    # approved without a revealed email (email drafting still enforces its
    # own email requirement). Company pages don't count — they can't be
    # messaged. This lets multi-channel outreach not miss anyone.
    if public_identifier_from_url(contact.linkedin_url or ""):
        blockers = [b for b in blockers if b != "email not revealed"]
    if blockers:
        raise ApprovalBlocked(
            f"Cannot approve: {', '.join(blockers)}. Automatic research/reveal "
            "was attempted but could not resolve this — Apollo or Anthropic may "
            "not have data for this prospect."
        )

    contact.approved_for_outreach = True
    log_action(
        db,
        AuditAction.PROSPECT_APPROVAL,
        entity_type="contact",
        entity_id=contact.id,
        actor=approved_by,
        summary="approved_for_outreach=True",
    )
    db.commit()


def reveal_contacts_bulk(db: Session, contacts: List[Contact]) -> int:
    """Reveal email/name/LinkedIn for many prospects in one batched pass.

    Built for bulk outreach mode: the caller has already pre-screened these
    contacts (rule-based sanity score + has_email), so we skip the per-contact
    board-fit gate used by :func:`reveal_contact`. Apollo's ``bulk_match`` is
    batched internally (10 per call) and only charges ~1 credit per contact it
    can return a verified email for. Returns the number of emails newly filled.
    """
    if not contacts:
        return 0

    provider = get_enrichment_provider()
    phone_reveal_enabled = bool(
        settings.apollo_reveal_phone_number and resolve_apollo_phone_webhook_url()
    )

    # Build enrichment payloads, keeping a parallel list so we can write results
    # back onto the right Contact row after the provider mutates each EC in place.
    pairs: list[tuple[Contact, EnrichmentContact]] = []
    for contact in contacts:
        if contact.email or contact.do_not_contact:
            continue
        if not contact.external_id and not contact.linkedin_url:
            continue
        company = getattr(contact, "company", None)
        domain = company.domain if company else None
        ec = EnrichmentContact(
            name=contact.name,
            title=contact.title,
            email=contact.email,
            external_id=contact.external_id,
            domain=domain,
            linkedin_url=contact.linkedin_url,
        )
        pairs.append((contact, ec))

    if not pairs:
        return 0

    provider.reveal_contacts([ec for _, ec in pairs])

    revealed = 0
    for contact, ec in pairs:
        if ec.email and not contact.email:
            contact.email = ec.email
            contact.email_status = ec.email_status
            revealed += 1
        elif ec.email_status and not contact.email_status:
            contact.email_status = ec.email_status
        if ec.phone:
            contact.phone = ec.phone
            contact.phone_reveal_status = PhoneRevealStatus.REVEALED
        elif phone_reveal_enabled and contact.external_id and not contact.phone:
            contact.phone_reveal_status = PhoneRevealStatus.PENDING
        if ec.name:
            contact.name = ec.name
        if ec.linkedin_url and not contact.linkedin_url:
            contact.linkedin_url = ec.linkedin_url
        if ec.location and not contact.location:
            contact.location = ec.location

    db.flush()
    log_action(
        db,
        AuditAction.ENRICHMENT,
        entity_type="contact",
        summary=f"Bulk reveal: {revealed} email(s) across {len(pairs)} prospect(s).",
    )
    return revealed


def _reveal_top_company_contacts(
    db: Session,
    company: Company,
    provider,
    limit: int,
    phone_reveal_enabled: bool,
) -> tuple[int, int]:
    """Reveal email/phone for up to ``limit`` existing contacts missing email."""
    if not settings.apollo_reveal_contacts or limit <= 0:
        return 0, 0

    ranked = sorted(
        company.contacts,
        key=lambda c: -(c.usefulness_score or 0),
    )[:limit]
    to_reveal: List[EnrichmentContact] = []
    targets: List[Contact] = []
    for contact in ranked:
        if not contact.external_id:
            continue
        needs_email = not contact.email
        needs_phone = bool(
            phone_reveal_enabled
            and not contact.phone
            and contact.phone_reveal_status
            not in (PhoneRevealStatus.PENDING, PhoneRevealStatus.REVEALED)
        )
        if not needs_email and not needs_phone:
            continue
        to_reveal.append(
            EnrichmentContact(
                name=contact.name,
                title=contact.title,
                email=contact.email,
                external_id=contact.external_id,
                domain=company.domain,
                linkedin_url=contact.linkedin_url,
            )
        )
        targets.append(contact)

    if not to_reveal:
        return 0, 0

    provider.reveal_contacts(to_reveal)
    revealed = 0
    phones_pending = 0
    for contact, ec in zip(targets, to_reveal):
        if ec.email and not contact.email:
            contact.email = ec.email
            contact.email_status = ec.email_status
            revealed += 1
        elif ec.email_status and not contact.email_status:
            contact.email_status = ec.email_status
        if ec.phone:
            contact.phone = ec.phone
            contact.phone_reveal_status = PhoneRevealStatus.REVEALED
        elif phone_reveal_enabled and contact.external_id:
            contact.phone_reveal_status = PhoneRevealStatus.PENDING
            phones_pending += 1
        if ec.name:
            contact.name = ec.name
        if ec.linkedin_url:
            contact.linkedin_url = ec.linkedin_url
        if ec.location:
            contact.location = ec.location

    db.flush()
    return revealed, phones_pending
