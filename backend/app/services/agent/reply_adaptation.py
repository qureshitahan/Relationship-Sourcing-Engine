"""Analyze replies against campaign goals and adapt A/B search variants."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.agent_playbook import AgentPlaybook
from app.models.agent_variant import AgentVariant
from app.models.contact import Contact
from app.models.email_draft import EmailDraft
from app.models.enums import EmailStatus
from app.models.principal import Principal
from app.services.agent.experiments import regenerate_variants

logger = logging.getLogger(__name__)

_GOAL_FIT_SYSTEM = """You judge whether an email reply indicates progress toward a B2B outreach goal.

Return JSON only:
{
  "goal_aligned": true/false,
  "score": 0.0-1.0,
  "summary": "one sentence"
}

goal_aligned=true when the reply shows interest, asks for a call, requests info, or engages constructively.
goal_aligned=false for out-of-office, wrong person, unsubscribe, hostile, or clearly off-topic."""


def _score_reply(goal: str, subject: str, reply_text: str) -> dict[str, Any]:
    text = (reply_text or "").strip()
    if not text:
        return {"goal_aligned": False, "score": 0.0, "summary": "Empty reply"}
    if not settings.anthropic_api_key:
        # Lightweight heuristic when no LLM.
        lower = text.lower()
        bad = ("unsubscribe", "remove me", "wrong person", "not interested", "stop emailing")
        good = ("interested", "let's", "happy to", "call", "schedule", "tell me more")
        if any(b in lower for b in bad):
            return {"goal_aligned": False, "score": 0.1, "summary": "Negative signal"}
        if any(g in lower for g in good):
            return {"goal_aligned": True, "score": 0.8, "summary": "Positive signal"}
        return {"goal_aligned": True, "score": 0.5, "summary": "Neutral reply"}

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        user = (
            f"CAMPAIGN GOAL:\n{goal}\n\n"
            f"ORIGINAL SUBJECT:\n{subject}\n\n"
            f"REPLY:\n{text[:4000]}"
        )
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=300,
            system=_GOAL_FIT_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        raw = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        )
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            return json.loads(raw[start : end + 1])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Reply goal scoring failed: %s", exc)
    return {"goal_aligned": True, "score": 0.5, "summary": "Could not score"}


def analyze_replies_and_adapt(
    db: Session,
    *,
    principal: Principal,
    playbook: Optional[AgentPlaybook],
    limit: int = 30,
) -> dict[str, Any]:
    """Score recent replies; retire weak A/B variants and seed a fresh one if needed."""
    goal = (playbook.objective_prompt if playbook else "") or (principal.objective or "")
    rows = db.execute(
        select(EmailDraft, Contact)
        .join(Contact, EmailDraft.contact_id == Contact.id)
        .where(
            EmailDraft.principal_id == principal.id,
            EmailDraft.status == EmailStatus.REPLIED,
        )
        .order_by(EmailDraft.replied_at.desc())
        .limit(limit)
    ).all()

    analyzed: list[dict[str, Any]] = []
    by_variant: dict[int, list[float]] = {}

    for draft, contact in rows:
        reply_text = draft.reply_body or draft.reply_snippet or ""
        fit = _score_reply(goal, draft.subject or "", reply_text)
        analyzed.append(
            {
                "contact_id": contact.id,
                "contact_name": contact.name,
                "variant_id": contact.variant_id,
                "score": fit.get("score", 0.5),
                "goal_aligned": fit.get("goal_aligned", True),
                "summary": fit.get("summary", ""),
            }
        )
        if contact.variant_id:
            by_variant.setdefault(contact.variant_id, []).append(
                float(fit.get("score", 0.5))
            )

    adaptations: list[str] = []
    if playbook and by_variant:
        for variant_id, scores in by_variant.items():
            if len(scores) < 2:
                continue
            avg = sum(scores) / len(scores)
            if avg >= 0.45:
                continue
            variant = db.get(AgentVariant, variant_id)
            if not variant or not variant.is_active:
                continue
            variant.is_active = False
            adaptations.append(
                f"Retired variant '{variant.label}' (avg reply-goal score {avg:.2f})"
            )
        if adaptations:
            db.commit()
            try:
                regenerate_variants(db, principal, playbook)
                adaptations.append("Seeded fresh A/B variants from playbook.")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Variant regeneration after reply analysis failed: %s", exc)

    return {
        "analyzed": len(analyzed),
        "replies": analyzed[:15],
        "adaptations": adaptations,
    }
