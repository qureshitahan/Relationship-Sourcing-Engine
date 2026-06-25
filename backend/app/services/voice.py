"""Voice call script + Vapi assistant prompt generation.

Generates transparent call scripts and system prompts for AI voice agents.
The agent must never pretend to be human: every prompt includes an explicit
AI-disclosure line. No calls are placed without human approval.
"""
from __future__ import annotations

from typing import Optional

from app.models.company import Company
from app.models.contact import Contact
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight

AI_DISCLOSURE = (
    "Yes, I am an AI assistant reaching out on behalf of {principal}. "
    "I can connect you with them directly if you would prefer."
)


def _first_name(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    return parts[0] if parts else "there"


def build_call_system_prompt(
    principal: Principal,
    company: Optional[Company],
    contact: Optional[Contact],
    insight: Optional[RelevanceInsight] = None,
) -> str:
    """System prompt for the Vapi assistant (Claude) on a live call."""
    org_name = (company.name if company else None) or "their organization"
    prospect = contact.name if contact else "the prospect"
    title = contact.title if contact else ""
    talking_points = (insight.talking_points or []) if insight else []
    snapshot = insight.snapshot if insight else None
    key_facts = (insight.key_facts or []) if insight else []

    lines = [
        f"You are a professional AI assistant calling on behalf of {principal.name}.",
        "Your goal is a brief, warm executive introduction, not a sales pitch.",
        "Rules:",
        "- Keep the call under 3 minutes unless they want to continue.",
        "- Be polite, concise, and respectful of their time.",
        "- If they ask whether you are AI, be honest immediately using the disclosure below.",
        "- If they are not interested, thank them and end the call.",
        "- If they are interested, ask for the best email or time for a follow-up with "
        f"{principal.name}.",
        "- Never claim to be {principal.name} or a human employee.",
        "",
        f"AI disclosure (use if asked): {AI_DISCLOSURE.format(principal=principal.name)}",
        "",
        f"Prospect: {prospect}" + (f", {title}" if title else ""),
        f"Organization: {org_name}",
    ]
    if snapshot:
        lines.extend(["", f"Who they are: {snapshot}"])
    if key_facts:
        lines.append("")
        lines.append("Researched facts (use at most one naturally):")
        for fact in key_facts[:3]:
            lines.append(f"- {fact}")
    if talking_points:
        lines.append("")
        lines.append("Conversation openers:")
        for tp in talking_points[:2]:
            lines.append(f"- {tp}")
    if principal.value_props:
        lines.append("")
        lines.append(f"Principal value: {', '.join(principal.value_props[:3])}")

    return "\n".join(lines)


def build_call_first_message(
    principal: Principal,
    contact: Optional[Contact],
    company: Optional[Company],
) -> str:
    """Opening line the voice agent speaks when the prospect answers."""
    name = _first_name(contact.name if contact else "")
    org_name = (company.name if company else None) or "your organization"
    return (
        f"Hi {name}, I am reaching out on behalf of {principal.name}. "
        f"They have been following {org_name} and would value a brief conversation "
        f"to explore a potential strategic connection. Do you have a moment?"
    )


def generate_call_script(
    principal: Principal,
    company: Optional[Company],
    contact: Optional[Contact],
    insight: Optional[RelevanceInsight] = None,
) -> str:
    """Human-readable script preview shown in the Call Queue UI."""
    org_name = (company.name if company else None) or "your organization"
    name = _first_name(contact.name if contact else "")
    connection = (
        insight.snapshot
        if insight and insight.snapshot
        else f"{principal.name}'s background and the work at {org_name}"
    )
    talking = ""
    if insight and insight.talking_points:
        talking = "\n".join(f"- {tp}" for tp in insight.talking_points[:2])

    script = (
        f"OPENING\n"
        f"{build_call_first_message(principal, contact, company)}\n\n"
        f"CONTEXT\n"
        f"{connection}\n"
    )
    if talking:
        script += f"\nTALKING POINTS\n{talking}\n"
    script += (
        f"\nIF ASKED ABOUT AI\n"
        f"{AI_DISCLOSURE.format(principal=principal.name)}\n\n"
        f"IF INTERESTED\n"
        f"Great, what is the best email or time for a short follow-up with {principal.name}?\n\n"
        f"IF NOT INTERESTED\n"
        f"Understood, thank you for your time. Have a great day."
    )
    return script
