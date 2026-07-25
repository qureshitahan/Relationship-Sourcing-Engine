"""Bridge between ORM models and the insight provider, with persistence."""
from __future__ import annotations

import re
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.company import Company
from app.models.contact import Contact
from app.models.enums import AuditAction
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.services.audit import log_action
from app.services.insights import get_insight_provider
from app.services.insights.base import OutreachResult


class InsightResearchError(RuntimeError):
    """Live research provider failed (rate limit / API error) and only a stub
    fallback was available. We refuse to persist a misleading score; the caller
    should surface this and allow a retry."""
from app.services.principal_docs import (
    build_principal_dossier,
    retrieve_relevant_proof_points,
)
from app.services.principal_docs.ingest import filter_usable_proof_points

# Resume-indexing artifacts that must never leak into a credential line.
_JUNK_PROOF_RE = re.compile(
    r"target roles identified|president,\s*ceo,\s*coo|"
    r"private equity operating partner,\s*managing partner",
    re.IGNORECASE,
)


def credential_summary(principal: Principal) -> str:
    """Short, human credential line for outreach — not a document dump."""
    parts: List[str] = []
    if principal.headline:
        parts.append(principal.headline.strip())
    for vp in principal.value_props or []:
        if vp and vp.strip() and not _JUNK_PROOF_RE.search(vp):
            parts.append(vp.strip())
            if len(parts) >= 2:
                break
    text = ". ".join(parts)
    if len(text) > 220:
        text = text[:217].rsplit(" ", 1)[0] + "…"
    return text


def principal_context(
    principal: Principal, *, outreach_goal: Optional[str] = None
) -> dict:
    ctx = {
        "name": principal.name,
        "headline": principal.headline,
        "document_focus": principal.document_focus,
        "bio": principal.bio,
        "background": principal.background,
        "focus_areas": principal.focus_areas,
        "target_sectors": principal.target_sectors,
        "investment_themes": principal.investment_themes,
        "acquisition_themes": principal.acquisition_themes,
        "value_props": principal.value_props,
        "opportunity_types": principal.opportunity_types,
        "geographies": principal.geographies,
    }
    goal = (outreach_goal or "").strip() or None
    if goal:
        ctx["outreach_goal"] = goal
        # Prompts still reference PRINCIPAL.objective in several places.
        ctx["objective"] = goal
    return ctx


def organization_context(company: Optional[Company]) -> Optional[dict]:
    if company is None:
        return None
    return {
        "name": company.name,
        "domain": company.domain,
        "industry": company.industry,
        "company_type": company.company_type,
        "sectors": company.sectors,
        "themes": company.themes,
        "signals": company.signals,
        "employee_count": company.employee_count,
        "headquarters": company.headquarters,
        "funding": company.funding,
        "revenue": company.revenue,
        "website": company.website,
        "linkedin_url": company.linkedin_url,
    }


def person_context(contact: Optional[Contact]) -> Optional[dict]:
    if contact is None:
        return None
    email_domain = None
    if contact.email and "@" in contact.email:
        email_domain = contact.email.split("@", 1)[1].lower()
    ctx = {
        "name": contact.name,
        "title": contact.title,
        "role_category": contact.role_category,
        "seniority": contact.seniority,
        "email": contact.email,
        "email_domain": email_domain,
        "location": contact.location,
        "linkedin_url": contact.linkedin_url,
    }
    if contact.usefulness_score is not None:
        ctx["discovery_match_score"] = contact.usefulness_score
    if contact.rank_reason:
        ctx["discovery_match_reason"] = contact.rank_reason
    return ctx


def generate_insight(
    db: Session,
    principal: Principal,
    *,
    contact: Optional[Contact] = None,
    company: Optional[Company] = None,
    outreach_goal: Optional[str] = None,
) -> RelevanceInsight:
    """Generate (or refresh) a RelevanceInsight for a principal + prospect/org."""
    if company is None and contact is not None and contact.company_id:
        company = db.get(Company, contact.company_id)

    provider = get_insight_provider()
    p_ctx = principal_context(principal, outreach_goal=outreach_goal)
    # Ground the research in the principal's own documents (resume, deals, etc.).
    dossier = build_principal_dossier(db, principal.id)
    if dossier:
        p_ctx["proof_points_from_documents"] = dossier
    result = provider.score_relevance(
        principal=p_ctx,
        organization=organization_context(company),
        person=person_context(contact),
    )

    # If the live provider degraded to a stub fallback (rate limit / API error),
    # do NOT persist a fabricated score. Surface it so the prospect stays
    # "not researched" and can be retried, rather than showing a bogus number.
    if result.generated_by and "stub fallback" in result.generated_by:
        raise InsightResearchError(
            "Live research failed (rate limit or API error). Please retry."
        )

    insight: Optional[RelevanceInsight] = None
    if contact is not None:
        insight = db.execute(
            select(RelevanceInsight).where(
                RelevanceInsight.principal_id == principal.id,
                RelevanceInsight.contact_id == contact.id,
            )
        ).scalar_one_or_none()

    if insight is None:
        insight = RelevanceInsight(
            principal_id=principal.id,
            contact_id=contact.id if contact else None,
            company_id=company.id if company else None,
        )
        db.add(insight)

    insight.relevance_score = result.relevance_score
    insight.why_relevant = result.why_relevant
    insight.why_speak_with_principal = result.why_speak_with_principal
    insight.strategic_connection = result.strategic_connection
    insight.common_ground = result.common_ground
    insight.relevant_experience = result.relevant_experience
    insight.signals = result.signals
    insight.talking_points = result.talking_points
    insight.snapshot = result.snapshot
    insight.key_facts = result.key_facts
    insight.sources = result.sources
    insight.identity_verified = result.identity_verified
    insight.identity_warnings = result.identity_warnings or None
    insight.opportunity_type = result.opportunity_type
    insight.generated_by = result.generated_by
    db.flush()

    # Denormalize the LATEST relevance onto the prospect for fast sorting.
    # (Always reflect the newest research so re-running corrects a bad/stale
    # score, e.g. a stub fallback that was later replaced by real research.)
    if contact is not None:
        contact.relevance_score = result.relevance_score
        # Only backfill LinkedIn when missing; never overwrite Apollo's URL.
        if result.linkedin_url and not contact.linkedin_url:
            contact.linkedin_url = result.linkedin_url

    log_action(
        db,
        AuditAction.INSIGHT,
        entity_type="relevance_insight",
        entity_id=insight.id,
        summary=(
            f"Scored relevance {result.relevance_score:.0f} for principal "
            f"{principal.name} via {result.generated_by}."
        ),
        detail={"opportunity_type": result.opportunity_type},
    )
    return insight


def _fact_text(item) -> str:
    """Normalize insight key_facts (string or {text, source_url} dict) to plain text."""
    if isinstance(item, dict):
        return (item.get("text") or "").strip()
    return str(item).strip() if item else ""


# Common closing words we strip from the end of a generated body before adding
# the canonical signature, so re-applying the signature never stacks.
_CLOSERS = {
    "thanks",
    "thank you",
    "best",
    "best regards",
    "regards",
    "warm regards",
    "kind regards",
    "cheers",
    "sincerely",
    "all the best",
    "warmly",
}


def resolve_linkedin_url(principal: Principal) -> str:
    """Principal profile URL for email signatures — no global fallback."""
    return (principal.linkedin_url or "").strip()


def principal_signature_ready(principal: Principal) -> bool:
    """True when we can build an outreach signature for this principal."""
    if (principal.email_signature or "").strip():
        return True
    name = (principal.name or "").strip()
    return (
        len(name.split()) >= 2
        and bool(resolve_linkedin_url(principal))
        and bool((principal.phone or "").strip())
    )


def build_signature(principal: Principal) -> str:
    """The email sign-off for this principal.

    Prefer the custom ``email_signature`` when set so each principal can own
    their full block (title, company, websites, phone). Otherwise fall back to
    the short default of Thanks / name / LinkedIn.
    """
    custom = (principal.email_signature or "").strip()
    if custom:
        return custom
    name = (principal.name or "").strip()
    lines = ["Thanks,"]
    if name:
        lines.append(name)
    url = resolve_linkedin_url(principal)
    if url:
        lines.append(url)
    return "\n".join(lines)


def _digits_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _strip_exact_signature(body: str, signature: str) -> str:
    """Remove ``signature`` when it is the exact trailing block in ``body``."""
    clean_body = (body or "").rstrip()
    block = (signature or "").strip()
    if not block:
        return clean_body
    if clean_body == block:
        return ""
    suffix = f"\n\n{block}"
    if clean_body.endswith(suffix):
        return clean_body[: -len(suffix)].rstrip()
    return clean_body


def apply_signature(
    body: str, principal: Principal, *, previous_signature: Optional[str] = None
) -> str:
    """Replace any trailing sign-off in ``body`` with the principal's signature.

    Idempotent: strips trailing blank/closer/name/URL/phone lines and any line
    that already appears in the target signature, so applying it repeatedly
    (e.g. after editing the principal signature) never stacks the block.
    """
    if not body:
        return body
    body = _strip_exact_signature(body, previous_signature or "")
    signature = build_signature(principal)
    signature_lines = {
        ln.strip().lower() for ln in signature.split("\n") if ln.strip()
    }
    name = (principal.name or "").strip().lower()
    first = name.split()[0] if name else ""
    phone = (principal.phone or "").strip()
    phone_digits = _digits_only(phone)
    lines = body.rstrip().split("\n")
    while lines:
        last = lines[-1].strip()
        normalized = last.lower().rstrip(",").strip()
        is_url = last.lower().startswith("http")
        is_name = bool(name) and (normalized == name or (first and normalized == first))
        is_closer = normalized in _CLOSERS
        is_phone = bool(phone_digits) and _digits_only(last) == phone_digits and len(last) < 40
        is_sig_line = bool(normalized) and normalized in signature_lines
        if last == "" or is_url or is_name or is_closer or is_phone or is_sig_line:
            lines.pop()
            continue
        break
    cleaned = "\n".join(lines).rstrip()
    if not signature:
        return cleaned
    return f"{cleaned}\n\n{signature}".strip()


def refresh_principal_draft_signatures(
    db: Session,
    principal: Principal,
    *,
    previous_signature: Optional[str] = None,
) -> int:
    """Re-apply ``principal``'s signature on every unsent draft. Returns count updated."""
    from app.models.email_draft import EmailDraft
    from app.models.enums import EmailStatus

    if not principal_signature_ready(principal):
        return 0
    drafts = db.execute(
        select(EmailDraft).where(
            EmailDraft.principal_id == principal.id,
            EmailDraft.status.in_(
                [EmailStatus.DRAFT, EmailStatus.APPROVED, EmailStatus.SCHEDULED]
            ),
        )
    ).scalars().all()
    updated = 0
    for draft in drafts:
        new_body = apply_signature(
            draft.body or "",
            principal,
            previous_signature=previous_signature,
        )
        if new_body != (draft.body or ""):
            draft.body = new_body
            updated += 1
    return updated


def generate_outreach(
    db: Session,
    principal: Principal,
    contact: Optional[Contact],
    company: Optional[Company],
    insight: Optional[RelevanceInsight],
    *,
    outreach_goal: Optional[str] = None,
    style: Optional[dict] = None,
) -> OutreachResult:
    """Generate a personalized outreach email (no persistence).

    ``style`` is an optional A/B copy directive (hook/structure/cta/tone/length)
    from the selected AgentCopyVariant; it steers HOW the email is written.
    """
    insight_ctx = None
    if insight is not None:
        facts = [_fact_text(f) for f in (insight.key_facts or []) if _fact_text(f)]
        insight_ctx = {
            "snapshot": insight.snapshot,
            "key_facts": facts,
            "why_relevant": insight.why_relevant,
            "talking_points": insight.talking_points,
            "opportunity_type": insight.opportunity_type,
        }

    provider = get_insight_provider()
    p_ctx = principal_context(principal, outreach_goal=outreach_goal)
    p_ctx["credential_summary"] = credential_summary(principal)
    # Keep the objective so the email advances the principal's specific goal,
    # but drop the raw document dossier (we feed it curated proof points instead).
    p_ctx.pop("proof_points_from_documents", None)

    # Per-person proof points from the principal's indexed documents, matched to
    # this prospect's world (title + company + industry + why-relevant).
    query_parts = [contact.title if contact else None]
    if company is not None:
        query_parts.append(company.industry)
    if insight is not None:
        query_parts.append(insight.why_relevant)
    query = " ".join(p for p in query_parts if p)
    if query:
        raw = retrieve_relevant_proof_points(db, principal.id, query, k=4)
        proof = filter_usable_proof_points(raw, max_items=2, max_len=120)
        if proof:
            p_ctx["relevant_proof_points"] = proof

    result = provider.generate_outreach(
        principal=p_ctx,
        person=person_context(contact),
        organization=organization_context(company),
        insight=insight_ctx,
        style=style,
    )
    # Enforce the standard signature regardless of how the LLM signed off.
    result.body = apply_signature(result.body, principal)
    return result


def generate_outreach_batch(
    db: Session,
    principal: Principal,
    contacts: list[Contact],
    *,
    outreach_goal: Optional[str] = None,
) -> list[tuple[Contact, OutreachResult]]:
    """Draft personalized first-touch emails one prospect at a time.

    Each draft is grounded in that prospect's own relevance insight (when one
    exists). This is the per-person path — no generic role-segment batching.
    """
    paired: list[tuple[Contact, OutreachResult]] = []
    for contact in contacts:
        company = db.get(Company, contact.company_id) if contact.company_id else None
        insight = db.execute(
            select(RelevanceInsight)
            .where(
                RelevanceInsight.principal_id == principal.id,
                RelevanceInsight.contact_id == contact.id,
            )
            .order_by(RelevanceInsight.created_at.desc())
        ).scalars().first()
        result = generate_outreach(
            db, principal, contact, company, insight, outreach_goal=outreach_goal
        )
        paired.append((contact, result))
    return paired


def generate_followup(
    db: Session,
    principal: Principal,
    contact: Optional[Contact],
    company: Optional[Company],
    insight: Optional[RelevanceInsight],
    previous: dict,
    step: int = 2,
) -> OutreachResult:
    """Generate a short follow-up to an unanswered email (no persistence).

    ``previous`` carries the prior email's subject/body so the model can bump
    the thread without repeating it. The subject is forced to a 'Re:' of the
    original so it reads as a continuation.
    """
    provider = get_insight_provider()
    insight_ctx = None
    if insight is not None:
        facts = [_fact_text(f) for f in (insight.key_facts or []) if _fact_text(f)]
        insight_ctx = {
            "snapshot": insight.snapshot,
            "key_facts": facts,
            "why_relevant": insight.why_relevant,
            "talking_points": insight.talking_points,
            "opportunity_type": insight.opportunity_type,
        }

    query_parts = [contact.title if contact else None]
    if company is not None:
        query_parts.append(company.industry)
    if insight is not None:
        query_parts.append(insight.why_relevant)
    query = " ".join(p for p in query_parts if p)

    p_ctx = principal_context(principal, outreach_goal=outreach_goal)
    relevant = retrieve_relevant_proof_points(db, principal.id, query, k=4)
    if relevant:
        p_ctx["relevant_proof_points"] = relevant

    result = provider.generate_followup(
        principal=p_ctx,
        person=person_context(contact),
        organization=organization_context(company),
        insight=insight_ctx,
        previous=previous,
        step=step,
    )

    prev_subject = (previous.get("subject") or "").strip()
    while prev_subject.lower().startswith("re:"):
        prev_subject = prev_subject[3:].strip()
    result.subject = f"Re: {prev_subject}" if prev_subject else "Following up"
    result.body = apply_signature(result.body, principal)
    return result
