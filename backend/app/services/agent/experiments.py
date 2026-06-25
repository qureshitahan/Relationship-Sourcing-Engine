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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
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


def ensure_variants(
    db: Session, principal: Principal, playbook
) -> list[AgentVariant]:
    """Return the active variants for a playbook, seeding them on first use."""
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
        return existing

    base_criteria = criteria_from_dict(playbook.criteria or {})
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
