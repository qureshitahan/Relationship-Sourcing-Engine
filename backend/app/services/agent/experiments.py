"""A/B search experiments for the autonomous agent.

The agent maintains several search **variants** per playbook and learns which
one converts best from real reply data. Selection is epsilon-greedy: untested
variants are tried first, then the best performer is exploited most of the time
with occasional exploration so we keep discovering better searches.
"""
from __future__ import annotations

import json
import logging
import random
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.agent_copy_variant import AgentCopyVariant
from app.models.agent_variant import AgentVariant
from app.models.contact import Contact
from app.models.email_draft import EmailDraft
from app.models.enums import EmailStatus
from app.models.principal import Principal
from app.services.agent.planner import criteria_from_dict

logger = logging.getLogger(__name__)

# Fraction of runs that explore (pick a non-best variant) instead of exploiting.
EXPLORE_EPSILON = 0.3
# How many generated variants to keep alongside the base search.
DEFAULT_VARIANT_COUNT = 3

_VARIANT_SYSTEM = """You design A/B test variants for a B2B people-search (Apollo.io).

Given a base ICP (titles, seniorities, industries, company_types, ...) and the
principal's objective, propose alternative search variants. Each variant changes
ONE axis at a time so we can learn which lever matters:
- a "titles" variant: a different but on-objective set of job titles
- a "seniorities" variant: a different seniority mix
- an "industries" variant: adjacent / alternative industries

Keep everything else equal to the base. Stay on-objective; never drift off-goal.

Return ONLY a JSON array, one object per variant:
[
  {
    "label": "short human label (<= 6 words)",
    "axis": "titles" | "seniorities" | "industries",
    "criteria": { ...full criteria with only that axis changed... },
    "rationale": "one sentence: why this might convert better"
  }
]
"""


def _generate_variant_specs(
    objective: str, base_criteria: dict, n: int
) -> list[dict]:
    """Ask the LLM for variant specs; fall back to simple perturbations."""
    if settings.anthropic_api_key:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            user = (
                f"OBJECTIVE:\n{objective}\n\n"
                f"BASE CRITERIA:\n{json.dumps(base_criteria, default=str)}\n\n"
                f"Propose {n} variants (vary one axis each)."
            )
            resp = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=1500,
                system=_VARIANT_SYSTEM,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", None) == "text"
            )
            start, end = text.find("["), text.rfind("]")
            if start != -1 and end != -1:
                data = json.loads(text[start : end + 1])
                specs = [s for s in data if isinstance(s, dict) and s.get("criteria")]
                if specs:
                    return specs[:n]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Variant LLM generation failed, using heuristic: %s", exc)

    return _heuristic_variant_specs(base_criteria, n)


def _heuristic_variant_specs(base_criteria: dict, n: int) -> list[dict]:
    """Cheap fallback: perturb one axis at a time."""
    specs: list[dict] = []
    titles = list(base_criteria.get("titles") or [])
    seniorities = list(base_criteria.get("seniorities") or [])

    # Titles variant: narrow to the most senior-sounding half.
    if len(titles) > 2:
        narrowed = titles[: max(1, len(titles) // 2)]
        specs.append(
            {
                "label": "Narrowed titles",
                "axis": "titles",
                "criteria": {**base_criteria, "titles": narrowed},
                "rationale": "Tighter title set may reach more relevant decision-makers.",
            }
        )

    # Seniorities variant: toggle director in/out.
    alt_sen = (
        [s for s in seniorities if s != "director"]
        if "director" in seniorities
        else seniorities + ["director"]
    )
    if alt_sen != seniorities:
        specs.append(
            {
                "label": "Alt seniority mix",
                "axis": "seniorities",
                "criteria": {**base_criteria, "seniorities": alt_sen},
                "rationale": "Different seniority mix to test reply rate.",
            }
        )

    return specs[:n]


#: Fields that define WHO a search targets. A change to any of them means the
#: existing variants are aimed at a different audience.
_ICP_FIELDS = (
    "titles", "seniorities", "industries", "company_types",
    "healthcare_sectors", "geographies", "keywords", "themes",
    "employee_min", "employee_max", "organization_domains",
    "organization_job_titles",
)


def _icp_fingerprint(criteria: dict) -> str:
    """Stable signature of who a criteria set targets (order-insensitive)."""
    parts = []
    for field in _ICP_FIELDS:
        val = (criteria or {}).get(field)
        if isinstance(val, list):
            val = sorted(str(v).strip().lower() for v in val if v)
        parts.append((field, val))
    return json.dumps(parts, sort_keys=True, default=str)


def ensure_variants(
    db: Session, principal: Principal, playbook
) -> list[AgentVariant]:
    """Return the active variants for a playbook, reseeding when the ICP changes.

    Variants used to be seeded once and then returned forever. Because
    ``execute_run`` searches with the SELECTED VARIANT's criteria rather than the
    playbook's, editing a campaign had no effect on what it actually searched
    for: a playbook moved from fintech to pharmaceuticals kept running the
    original fintech variants — including the one labelled "Base search" — so
    every run returned fintech CEOs, every one scored below 40 against a pharma
    objective, and the campaign looked broken while the edit sat there ignored.

    So compare what the variants target against what the playbook now says. If
    the ICP has moved, retire the old set (their reply-rate stats describe a
    different audience and must not steer selection) and seed a fresh one.
    """
    base_criteria = criteria_from_dict(playbook.criteria or {})
    want = _icp_fingerprint(base_criteria)

    existing = db.execute(
        select(AgentVariant)
        .where(
            AgentVariant.principal_id == principal.id,
            AgentVariant.playbook_id == playbook.id,
            AgentVariant.is_active.is_(True),
        )
        .order_by(AgentVariant.id.asc())
    ).scalars().all()
    if existing:
        base = next((v for v in existing if v.axis == "base"), existing[0])
        if _icp_fingerprint(base.criteria or {}) == want:
            return existing
        logger.info(
            "Playbook %s ICP changed; retiring %s stale variant(s) and reseeding",
            playbook.id, len(existing),
        )
        for v in existing:
            v.is_active = False
        db.commit()
    base = AgentVariant(
        principal_id=principal.id,
        playbook_id=playbook.id,
        label="Base search",
        axis="base",
        criteria=base_criteria,
        rationale="The playbook's original ICP.",
    )
    db.add(base)
    variants = [base]
    for spec in _generate_variant_specs(
        playbook.objective_prompt or "", base_criteria, DEFAULT_VARIANT_COUNT
    ):
        variants.append(
            AgentVariant(
                principal_id=principal.id,
                playbook_id=playbook.id,
                label=(spec.get("label") or "Variant")[:255],
                axis=spec.get("axis"),
                criteria=criteria_from_dict(spec.get("criteria") or {}),
                rationale=spec.get("rationale"),
            )
        )
    db.add_all(variants[1:])
    db.commit()
    for v in variants:
        db.refresh(v)
    return variants


def regenerate_variants(
    db: Session, principal: Principal, playbook
) -> list[AgentVariant]:
    """Deactivate current variants and seed a fresh set from the playbook."""
    current = db.execute(
        select(AgentVariant).where(
            AgentVariant.principal_id == principal.id,
            AgentVariant.playbook_id == playbook.id,
            AgentVariant.is_active.is_(True),
        )
    ).scalars().all()
    for v in current:
        v.is_active = False
    db.commit()
    return ensure_variants(db, principal, playbook)


def _reply_open_counts(db: Session, variant_id: int) -> tuple[int, int, int]:
    """Return (sent, opened, replied) emails for a variant's contacts."""
    sent = db.execute(
        select(func.count())
        .select_from(EmailDraft)
        .join(Contact, EmailDraft.contact_id == Contact.id)
        .where(
            Contact.variant_id == variant_id,
            EmailDraft.status.in_([EmailStatus.SENT, EmailStatus.REPLIED]),
        )
    ).scalar_one()
    opened = db.execute(
        select(func.count())
        .select_from(EmailDraft)
        .join(Contact, EmailDraft.contact_id == Contact.id)
        .where(
            Contact.variant_id == variant_id,
            EmailDraft.open_count > 0,
        )
    ).scalar_one()
    replied = db.execute(
        select(func.count())
        .select_from(EmailDraft)
        .join(Contact, EmailDraft.contact_id == Contact.id)
        .where(
            Contact.variant_id == variant_id,
            EmailDraft.status == EmailStatus.REPLIED,
        )
    ).scalar_one()
    return sent, opened, replied


def variant_stats(db: Session, variant: AgentVariant) -> dict:
    sent, opened, replied = _reply_open_counts(db, variant.id)
    reply_rate = (replied / sent) if sent else 0.0
    open_rate = (opened / sent) if sent else 0.0
    return {
        "id": variant.id,
        "label": variant.label,
        "axis": variant.axis,
        "criteria": variant.criteria,
        "rationale": variant.rationale,
        "is_active": variant.is_active,
        "runs": variant.runs,
        "discovered": variant.discovered,
        "drafted": variant.drafted,
        "sent": sent,
        "opened": opened,
        "replied": replied,
        "reply_rate": round(reply_rate, 4),
        "open_rate": round(open_rate, 4),
    }


def select_variant(db: Session, variants: list[AgentVariant]) -> AgentVariant:
    """Epsilon-greedy pick: untested first, then mostly the best reply rate."""
    if not variants:
        raise ValueError("No variants to select from")

    # Always try an untested variant first (pure exploration of the unknown).
    untested = [v for v in variants if (v.runs or 0) == 0]
    if untested:
        return random.choice(untested)

    # Explore occasionally so a currently-trailing variant can still win.
    if len(variants) > 1 and random.random() < EXPLORE_EPSILON:
        return random.choice(variants)

    # Exploit: highest reply rate, breaking ties by lower volume (give it room).
    def score(v: AgentVariant) -> tuple[float, float]:
        sent, _opened, replied = _reply_open_counts(db, v.id)
        rate = (replied / sent) if sent else 0.0
        return (rate, -float(sent))

    return max(variants, key=score)


# ---------------------------------------------------------------------------
# Email-COPY A/B variants (the second optimization lever).
#
# Where AgentVariant tests WHO we target, AgentCopyVariant tests HOW the email is
# written (hook, structure, CTA, tone, length). Each draft is tagged with the
# copy variant that wrote it, so the same epsilon-greedy + reply-learning loop
# can discover which message converts best.
# ---------------------------------------------------------------------------

_COPY_VARIANT_SYSTEM = """You design A/B test variants for a B2B cold outreach EMAIL (not the search).

Given the campaign objective, propose distinct WRITING approaches. Each variant changes
the message style — not who we contact. Vary things like:
- hook: how the first line earns attention (shared-context, specific-observation,
  curiosity question, direct value, mutual-interest).
- structure: order and rhythm (one-line punch vs short paragraph vs two beats).
- cta: the single ask (quick call, reply with a yes, share a pointer, low-friction question).
- tone: peer-to-peer, warm-professional, concise-executive, curious-builder.
- length: very-short (2-3 lines) vs short (4-5 lines).

Keep every variant honest, non-spammy, first-person (the principal writing themselves),
and on-objective. Return ONLY a JSON array, one object per variant:
[
  {
    "label": "short human label (<= 5 words)",
    "style": {"hook": "...", "structure": "...", "cta": "...", "tone": "...", "length": "..."},
    "rationale": "one sentence: why this angle might earn more replies"
  }
]
"""

# How many copy variants to keep per playbook (including the baseline).
DEFAULT_COPY_VARIANT_COUNT = 3


def _heuristic_copy_specs(n: int) -> list[dict]:
    """LLM-free fallback set of distinct, sensible copy approaches."""
    specs = [
        {
            "label": "Direct peer-to-peer",
            "style": {
                "hook": "specific-observation",
                "structure": "one-line punch",
                "cta": "quick call",
                "tone": "peer-to-peer",
                "length": "very-short",
            },
            "rationale": "A crisp, specific opener respects the reader's time.",
        },
        {
            "label": "Curiosity question",
            "style": {
                "hook": "curiosity-question",
                "structure": "two beats",
                "cta": "low-friction question",
                "tone": "curious-builder",
                "length": "short",
            },
            "rationale": "An open question invites an easy reply.",
        },
        {
            "label": "Shared-context warm",
            "style": {
                "hook": "shared-context",
                "structure": "short paragraph",
                "cta": "reply with a yes",
                "tone": "warm-professional",
                "length": "short",
            },
            "rationale": "Leading with common ground builds trust fast.",
        },
    ]
    return specs[:n]


def _generate_copy_specs(objective: str, n: int) -> list[dict]:
    """Ask the LLM for copy-variant specs; fall back to heuristics."""
    if settings.anthropic_api_key:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            user = (
                f"OBJECTIVE:\n{objective}\n\n"
                f"Propose {n} distinct email writing approaches (vary hook/structure/cta/tone/length)."
            )
            resp = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=1200,
                system=_COPY_VARIANT_SYSTEM,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", None) == "text"
            )
            start, end = text.find("["), text.rfind("]")
            if start != -1 and end != -1:
                data = json.loads(text[start : end + 1])
                specs = [s for s in data if isinstance(s, dict) and s.get("style")]
                if specs:
                    return specs[:n]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Copy variant LLM generation failed, using heuristic: %s", exc)
    return _heuristic_copy_specs(n)


def ensure_copy_variants(
    db: Session, principal: Principal, playbook
) -> list[AgentCopyVariant]:
    """Return active copy variants for a playbook, seeding them on first use."""
    existing = db.execute(
        select(AgentCopyVariant)
        .where(
            AgentCopyVariant.principal_id == principal.id,
            AgentCopyVariant.playbook_id == playbook.id,
            AgentCopyVariant.is_active.is_(True),
        )
        .order_by(AgentCopyVariant.id.asc())
    ).scalars().all()
    if existing:
        return existing

    specs = _generate_copy_specs(
        playbook.objective_prompt or "", DEFAULT_COPY_VARIANT_COUNT
    )
    variants = [
        AgentCopyVariant(
            principal_id=principal.id,
            playbook_id=playbook.id,
            label=(spec.get("label") or "Copy variant")[:255],
            style=spec.get("style") or {},
            rationale=spec.get("rationale"),
        )
        for spec in specs
    ]
    if not variants:
        variants = [
            AgentCopyVariant(
                principal_id=principal.id,
                playbook_id=playbook.id,
                label="Default copy",
                style={},
                rationale="Baseline writing approach.",
            )
        ]
    db.add_all(variants)
    db.commit()
    for v in variants:
        db.refresh(v)
    return variants


def regenerate_copy_variants(
    db: Session, principal: Principal, playbook
) -> list[AgentCopyVariant]:
    """Deactivate current copy variants and seed a fresh set."""
    current = db.execute(
        select(AgentCopyVariant).where(
            AgentCopyVariant.principal_id == principal.id,
            AgentCopyVariant.playbook_id == playbook.id,
            AgentCopyVariant.is_active.is_(True),
        )
    ).scalars().all()
    for v in current:
        v.is_active = False
    db.commit()
    return ensure_copy_variants(db, principal, playbook)


def _copy_reply_counts(db: Session, copy_variant_id: int) -> tuple[int, int, int]:
    """Return (sent, opened, replied) emails written by a copy variant."""
    sent = db.execute(
        select(func.count())
        .select_from(EmailDraft)
        .where(
            EmailDraft.copy_variant_id == copy_variant_id,
            EmailDraft.status.in_([EmailStatus.SENT, EmailStatus.REPLIED]),
        )
    ).scalar_one()
    opened = db.execute(
        select(func.count())
        .select_from(EmailDraft)
        .where(
            EmailDraft.copy_variant_id == copy_variant_id,
            EmailDraft.open_count > 0,
        )
    ).scalar_one()
    replied = db.execute(
        select(func.count())
        .select_from(EmailDraft)
        .where(
            EmailDraft.copy_variant_id == copy_variant_id,
            EmailDraft.status == EmailStatus.REPLIED,
        )
    ).scalar_one()
    return sent, opened, replied


def copy_variant_stats(db: Session, variant: AgentCopyVariant) -> dict:
    sent, opened, replied = _copy_reply_counts(db, variant.id)
    return {
        "id": variant.id,
        "label": variant.label,
        "style": variant.style,
        "rationale": variant.rationale,
        "is_active": variant.is_active,
        "drafted": variant.drafted,
        "sent": sent,
        "opened": opened,
        "replied": replied,
        "reply_rate": round((replied / sent) if sent else 0.0, 4),
        "open_rate": round((opened / sent) if sent else 0.0, 4),
    }


def select_copy_variant(
    db: Session, variants: list[AgentCopyVariant]
) -> Optional[AgentCopyVariant]:
    """Epsilon-greedy pick of an email-copy approach (untested first, then best)."""
    active = [v for v in variants if v.is_active]
    if not active:
        return None

    untested = [v for v in active if (v.drafted or 0) == 0]
    if untested:
        return random.choice(untested)

    if len(active) > 1 and random.random() < EXPLORE_EPSILON:
        return random.choice(active)

    def score(v: AgentCopyVariant) -> tuple[float, float]:
        sent, _opened, replied = _copy_reply_counts(db, v.id)
        rate = (replied / sent) if sent else 0.0
        return (rate, -float(sent))

    return max(active, key=score)


def best_send_bucket(db: Session, principal: Principal) -> Optional[int]:
    """Return the send-time bucket index with the best reply rate, or None.

    Reads replies vs sends grouped by the recorded send_bucket_index so the agent
    can bias future sends toward the window that earns more responses. Requires a
    minimum sample so we don't over-fit early noise.
    """
    rows = db.execute(
        select(
            EmailDraft.send_bucket_index,
            func.count().label("sent"),
            func.sum(
                case((EmailDraft.status == EmailStatus.REPLIED, 1), else_=0)
            ).label("replied"),
        )
        .where(
            EmailDraft.principal_id == principal.id,
            EmailDraft.send_bucket_index.isnot(None),
            EmailDraft.status.in_([EmailStatus.SENT, EmailStatus.REPLIED]),
        )
        .group_by(EmailDraft.send_bucket_index)
    ).all()

    best_idx: Optional[int] = None
    best_rate = -1.0
    total_sent = 0
    for bucket_idx, sent, replied in rows:
        total_sent += int(sent or 0)
        rate = (float(replied or 0) / sent) if sent else 0.0
        if rate > best_rate:
            best_rate = rate
            best_idx = int(bucket_idx)
    # Need a meaningful sample before trusting a winner.
    if total_sent < 8:
        return None
    return best_idx
