"""Draft the Classic Search LinkedIn copy from a campaign goal, with Claude.

The one place a model is used in this module. It writes the two boxes on the
page — the invitation note and the message — and then gets out of the way: what
it produces lands in the form for the user to read and edit, and the send path
still transmits whatever is in those boxes verbatim. Nothing generated here goes
to anybody without a human pressing Draft and then Send.

Two constraints are not style preferences and are enforced in the prompt because
the page cannot fix them afterwards:

* The invitation note is sent to EVERYONE unchanged — no name is substituted —
  so it must not contain a greeting with a name or a ``[placeholder]``.
* The message has ``Hi <first name>,`` prepended by ``build_dm``, so it must not
  open with its own greeting or the recipient's name appears twice.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from app.core.config import settings
from app.services.provider_health import (
    inspect_anthropic_exception,
    record_provider_success,
)

logger = logging.getLogger(__name__)


class CopyError(Exception):
    """Raised with a message worth showing the user as-is."""


SYSTEM = """You write LinkedIn outreach copy for a B2B sales campaign.

You produce exactly two pieces of text.

1. INVITATION NOTE — attached to a connection request.
   - Hard limit: {note_max} characters. Count them. Going over means the end is
     cut off mid-sentence in the real request.
   - It is sent to every recipient UNCHANGED. There is no name substitution, so
     never write a greeting with a name, never write "Hi [First name]", and
     never use square brackets or any other placeholder.
   - One or two sentences. Say what the sender does and why they are reaching
     out to this kind of person. Do not pitch, do not ask for a meeting — the
     only thing being asked for here is the connection.

2. MESSAGE — the direct message, sent after the connection is accepted.
   - The application automatically puts "Hi <first name>," on the first line, so
     your text must NOT begin with a greeting or the recipient's name. Start
     with the first real sentence.
   - Some recipients are already 1st-degree connections and get this message
     with no connection request first, so do not open with "Thanks for
     connecting" or assume they just accepted anything.
   - 90-140 words. Plain sentences, blank lines between paragraphs, no markdown,
     no bullet points, no emoji, no subject line, no sign-off or signature.
   - Say who the sender is, what specifically is offered, and end with one clear
     low-friction ask.
   - It is sent to everyone unchanged, so nothing person-specific and no
     placeholders.

Write like a person, not a brochure. Concrete beats clever: name the actual work
being automated or the actual problem being solved rather than talking about
"solutions" and "synergies". No hype adjectives.

Respond with ONLY a JSON object, no prose around it, no code fences:
{{"invitation_note": "...", "message": "..."}}"""


def _client():
    if not (settings.anthropic_api_key or "").strip():
        raise CopyError(
            "No Anthropic API key is configured, so drafting cannot run. "
            "Write the note and message yourself, or set ANTHROPIC_API_KEY."
        )
    import anthropic  # imported lazily so the package stays optional

    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _parse(text: str) -> dict:
    """Pull the JSON object out of the reply.

    Fenced or prefaced output still parses: the model is told not to do either,
    but a rejected draft because of a stray ``` is a worse outcome than being
    lenient here.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except (TypeError, ValueError):
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except (TypeError, ValueError):
            pass
    raise CopyError("The model did not return usable copy. Try again.")


def generate_copy(
    *,
    goal: str,
    note_max_chars: int,
    job_titles: Optional[list[str]] = None,
    keywords: Optional[str] = None,
) -> dict:
    """Draft an invitation note and a message from a campaign goal.

    The filters already on screen are handed over as context, because "who this
    is going to" is most of what makes the copy land, and the user has already
    said it once by choosing them.
    """
    goal = (goal or "").strip()
    if not goal:
        raise CopyError("Write the campaign goal first — that is what gets drafted from.")

    audience: list[str] = []
    if job_titles:
        audience.append("Titles being targeted: " + ", ".join(job_titles))
    if (keywords or "").strip():
        audience.append(f"Search keywords: {keywords.strip()}")

    user = goal if not audience else goal + "\n\n" + "\n".join(audience)

    try:
        client = _client()
        resp = client.messages.create(
            model=settings.linkedin_copy_model,
            max_tokens=2000,
            system=SYSTEM.format(note_max=note_max_chars),
            messages=[{"role": "user", "content": user}],
        )
    except CopyError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
        logger.warning("LinkedIn copy generation failed: %s", exc)
        # Feeds the provider banner, so an exhausted key says so app-wide rather
        # than looking like this one button is broken.
        detail = inspect_anthropic_exception(exc)
        raise CopyError(detail or f"Claude could not be reached: {exc}") from exc

    text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
    parsed = _parse(text)
    record_provider_success("anthropic")

    note = " ".join(str(parsed.get("invitation_note") or "").split()).strip()
    message = str(parsed.get("message") or "").strip()
    if not note or not message:
        raise CopyError("The model returned only part of the copy. Try again.")

    # The limit is enforced here as well as in the prompt. A model that overruns
    # by a few characters would otherwise have its note silently cut at send
    # time, which is exactly the surprise this whole box exists to remove.
    over_by = max(0, len(note) - note_max_chars)
    if over_by:
        note = note[:note_max_chars].rstrip()
        # Back off to the last sentence end so the cut does not land mid-word.
        for stop in (". ", "! ", "? "):
            cut = note.rfind(stop)
            if cut > note_max_chars * 0.6:
                note = note[: cut + 1]
                break
    return {
        "invitation_note": note,
        "message": message,
        "note_chars": len(note),
        "note_trimmed": bool(over_by),
    }
