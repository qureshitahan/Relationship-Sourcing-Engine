"""Anthropic Claude insight + personalization provider.

Generates structured strategic relevance assessments and warm, personalized
outreach grounded in the principal's background and the prospect's signals.

Falls back to the stub provider when no API key is configured or on any error,
so the rest of the platform keeps working.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

from app.core.config import settings
from app.services.insights.base import (
    DocumentIndex,
    InsightProvider,
    InsightResult,
    OutreachResult,
)
from app.services.insights.outreach_prompts import OUTREACH_SINGLE_SYSTEM
from app.services.insights.stub import StubInsightProvider
from app.services.provider_health import (
    inspect_anthropic_exception,
    mark_using_stub,
    record_provider_success,
)

logger = logging.getLogger(__name__)


_INSIGHT_SYSTEM = (
    "You are a research analyst preparing a one-page brief for a senior executive "
    "(the principal) before they reach out to a prospect.\n\n"
    "IDENTITY VERIFICATION (do this first, before writing the brief):\n"
    "1. If PROSPECT.linkedin_url is provided, open that exact LinkedIn profile via "
    "web_search. It is the PRIMARY source of truth for name, title, employer, and "
    "location. Your snapshot and key_facts MUST describe the person on THAT profile.\n"
    "2. Compare the LinkedIn employer and location to PROSPECT'S ORGANIZATION in our "
    "database (name, HQ, domain). Apollo/CRM data is often wrong or conflated.\n"
    "3. If LinkedIn employer differs from the database org (e.g. UK recruitment agency "
    "vs US healthcare services company), set identity_verified=false, list the "
    "specific conflict in identity_warnings, and write facts from LinkedIn only. "
    "Do NOT describe the wrong company from the database.\n"
    "4. If you find the person on LinkedIn via web search and name + employer + title "
    "align with PROSPECT data, set identity_verified=true even when CRM lacked a "
    "LinkedIn URL or org details. Sparse CRM records are normal — do not treat them "
    "as identity conflicts.\n"
    "5. Set identity_verified=false ONLY for real conflicts: wrong employer, wrong "
    "country, or a clearly different person. Common names with a matching LinkedIn "
    "profile are identity_verified=true with optional soft warnings.\n"
    "6. Use email domain and location hints to catch mismatches (e.g. .org US domain "
    "vs UK LinkedIn location).\n\n"
    "Then write a SHORT, scannable brief. Rules:\n"
    "- Be concise. Bullets, not paragraphs. No filler, no flattery, no sales-speak.\n"
    "- 'key_facts' = 3 to 6 researched bullets. Each bullet is an object:\n"
    "  {\"text\": \"...\", \"source_url\": \"https://...\", \"source_title\": \"short label\", "
    "\"source_date\": \"YYYY-MM or YYYY-MM-DD when known, else null\"}\n"
    "  Wrap important names, titles, companies, dates, and metrics in **double asterisks** "
    "(markdown bold). Every key_fact MUST include the exact source_url you used. "
    "Prefer LinkedIn and the firm's current website over old press pages that may 404.\n"
    "- 'fit' = honest mapping to PRINCIPAL.outreach_goal (the active search objective). "
    "If the prospect's title matches the goal's target roles (e.g. ML Engineer when "
    "searching for AI engineers), that is STRONG fit — score accordingly. "
    "Do NOT default to board-seat framing unless the goal says board/director. "
    "2 to 4 strings, each <= 25 words. Wrap key judgment phrases in **bold**.\n"
    "- 'snapshot' = one line: verified name, title, employer, location. "
    "Bold the person's name and current employer with **markdown**.\n"
    "- 'talking_points' = 2 to 3 short openers grounded in verified facts. "
    "Wrap the hook phrase in **bold**.\n"
    "- 'linkedin_url' = confirmed profile URL (prefer the provided one if it matches).\n"
    "- 'identity_verified' = true when LinkedIn confirms name, employer, and title "
    "with no material conflict. False only for real mismatches.\n"
    "- 'identity_warnings' = soft notes only (common name, sparse CRM); empty if clean.\n"
    "- 'relevance_score' = 0-100 vs PRINCIPAL.outreach_goal. Examples: title matches "
    "search roles + same domain as principal = 70-90; partial overlap = 50-69; "
    "wrong function or sector = below 40. Only score <= 30 for clear wrong-person "
    "or major goal mismatch — NOT for sparse CRM metadata.\n\n"
    "After researching, respond with ONLY a single JSON object (no prose around it)."
)

_OUTREACH_SYSTEM = OUTREACH_SINGLE_SYSTEM

_FOLLOWUP_SYSTEM = (
    "You write a brief, friendly follow-up to a previous cold outreach email that "
    "received no reply. The email is sent BY the PRINCIPAL, who is pursuing the goal in "
    "PRINCIPAL.objective; the RECIPIENT is a person relevant to that goal. People are "
    "busy, so assume good intent, never guilt or pressure.\n\n"
    "VOICE (CRITICAL): You ARE the PRINCIPAL writing this yourself. Write in the FIRST "
    "PERSON ('I', 'my'). NEVER refer to the principal in the third person or by name "
    "(write 'I have a track record in...', NEVER 'Dalbir has...'). The name appears only "
    "in the signature, added later.\n\n"
    "WRITE THE BODY AS 2-3 SHORT LINES (shorter than the first email):\n"
    "1) Greeting: 'Hi <FirstName>,'\n"
    "2) A light nudge that references the earlier note in ONE clause "
    "(e.g. 'wanted to gently resurface my note'), then add ONE fresh, specific angle, "
    "value, or thought, in the first person. Do NOT repeat the first email's content.\n"
    "3) ONE soft, low-friction ask (e.g. 'Worth a quick hello?').\n\n"
    "HARD RULES:\n"
    "- Keep it shorter than the original. Never sound annoyed or use guilt "
    "('I haven't heard back', 'just following up again').\n"
    "- Do NOT add a sign-off, name, or signature. End on the question.\n"
    "- NEVER use em or en dashes. No links. One ask only. Do not invent facts.\n"
    "Respond with ONLY JSON: {\"body\": \"...\"}."
)

_REPLY_SYSTEM = (
    "You write the PRINCIPAL's reply to an inbound email from a prospect. The "
    "prospect already replied to our outreach — your job is to move toward a "
    "short meeting while sounding human, specific, and useful.\n\n"
    "VOICE (CRITICAL): You ARE the PRINCIPAL writing in the FIRST PERSON ('I', "
    "'my'). NEVER refer to the principal in the third person or by name. The "
    "signature is added later — do NOT add a sign-off, name, or signature.\n\n"
    "READ THEIR REPLY CAREFULLY:\n"
    "- Acknowledge what they actually said (hesitation, interest, objection, "
    "pivot, pain point, timing) in one short clause. Do not ignore it.\n"
    "- If they named a pain point, priority, or focus area, pivot the value of "
    "a call to THAT — not a generic restatement of the first email.\n"
    "- If they are unsure a call is worth their time, reduce friction and show "
    "why it would be useful for THEM, briefly.\n"
    "- If they declined or asked not to be contacted, politely close — do not "
    "push for a meeting.\n"
    "- If they agreed to talk, propose a concrete next step (times / calendar).\n\n"
    "STRUCTURE (3-5 short lines):\n"
    "1) Greeting: 'Hi <FirstName>,'\n"
    "2) Acknowledge their point + one specific, useful response grounded in "
    "PRINCIPAL.objective / proof points / insight when relevant.\n"
    "3) ONE clear, soft CTA aimed at booking a short call (unless they declined).\n\n"
    "HARD RULES:\n"
    "- Do NOT invent facts, clients, numbers, or capabilities.\n"
    "- Do NOT paste or quote their whole email back at them.\n"
    "- NEVER use em or en dashes. No markdown. Prefer no links unless a "
    "calendly/URL is in PRINCIPAL context and clearly helpful.\n"
    "- Keep it concise — this is a reply, not a pitch deck.\n"
    "Respond with ONLY JSON: {\"body\": \"...\"}."
)


class AnthropicInsightProvider(InsightProvider):
    name = "anthropic"

    def __init__(self) -> None:
        self.api_key = settings.anthropic_api_key
        self.model = settings.anthropic_model
        self._fallback = StubInsightProvider()
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic  # imported lazily so the package is optional

            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def _complete_json(self, system: str, user: str) -> Optional[dict]:
        try:
            client = self._get_client()
            resp = client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(
                block.text for block in resp.content if getattr(block, "type", None) == "text"
            )
            parsed = _parse_json(text)
            record_provider_success("anthropic")
            return parsed
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            logger.warning("Anthropic insight call failed: %s", exc)
            inspect_anthropic_exception(exc)
            return None

    def _research_json(
        self, system: str, user: str, *, max_uses: Optional[int] = None
    ) -> Optional[Any]:
        """Like _complete_json but lets Claude web-search to ground the brief.

        Returns parsed JSON (object or array). Also collects web sources on objects.
        """
        web_max = (
            settings.insight_web_search_max_uses
            if max_uses is None
            else max_uses
        )
        if web_max <= 0:
            return self._complete_json(system, user)
        client = self._get_client()
        resp = None
        # Retry transient failures (rate limits / overloaded) with backoff before
        # degrading. Parallel batch research can briefly exceed Anthropic limits.
        max_attempts = 4
        for attempt in range(max_attempts):
            try:
                resp = client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=system,
                    tools=[
                        {
                            "type": "web_search_20250305",
                            "name": "web_search",
                            "max_uses": web_max,
                        }
                    ],
                    messages=[{"role": "user", "content": user}],
                )
                break
            except Exception as exc:  # noqa: BLE001
                transient = _is_transient_error(exc)
                if transient and attempt < max_attempts - 1:
                    delay = 2.0 * (2**attempt)  # 2s, 4s, 8s
                    logger.warning(
                        "Anthropic web-search rate/transient error (%s); retry %d/%d in %.0fs",
                        exc,
                        attempt + 1,
                        max_attempts - 1,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                logger.warning(
                    "Anthropic web-search insight failed (%s); retrying without search", exc
                )
                inspect_anthropic_exception(exc)
                return self._complete_json(system, user)
        if resp is None:
            return self._complete_json(system, user)

        text_parts: list[str] = []
        sources: list[dict] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "web_search_tool_result":
                for item in getattr(block, "content", []) or []:
                    # Result items come back as dicts (sometimes objects).
                    if isinstance(item, dict):
                        url = item.get("url")
                        title = item.get("title")
                    else:
                        url = getattr(item, "url", None)
                        title = getattr(item, "title", None)
                    if url:
                        sources.append({"title": title or url, "url": url})

        data = _parse_json_value("".join(text_parts))
        if isinstance(data, dict) and sources and not data.get("sources"):
            # De-dup sources by url, cap to keep the brief tidy.
            seen, deduped = set(), []
            for s in sources:
                if s["url"] not in seen:
                    seen.add(s["url"])
                    deduped.append(s)
            data["sources"] = deduped[:6]
        if data is not None:
            record_provider_success("anthropic")
        return data

    def index_document(
        self,
        *,
        text: str,
        filename: str,
        principal: Optional[dict] = None,
    ) -> DocumentIndex:
        if not self.api_key:
            return self._fallback.index_document(
                text=text, filename=filename, principal=principal
            )
        principal_blob = json.dumps(principal or {}, indent=2, default=str)
        system = (
            "You index documents for a principal's profile. PRINCIPAL.document_focus is an "
            "optional niche to emphasize (e.g. 'AI Engineering', 'Data Analysis'). If set, "
            "prioritize proof points and themes in that area; down-rank unrelated sections. "
            "If document_focus is blank, extract all professionally relevant credentials, "
            "achievements, and themes without forcing a single niche.\n\n"
            "Your job:\n"
            "1. Judge how RELEVANT this file is (relevance_score 0–100).\n"
            "2. Extract ONLY content that helps position the principal.\n"
            "3. Ignore unrelated sections — do not force-fit noise into key_facts.\n\n"
            "Relevance guidance:\n"
            "- 80–100: core (resume, bio, deal sheets, case studies, credentials on-focus).\n"
            "- 55–79: useful but narrow (press clip, one case study, partial overlap).\n"
            "- 35–54: peripheral (mostly unrelated but 1–2 usable proof points buried inside).\n"
            "- 0–34: irrelevant (wrong person, personal/unrelated, no usable signal).\n\n"
            "For MIXED documents: extract only the on-topic lines; set relevance_score "
            "from the useful fraction; explain in relevance_note what was ignored.\n"
            "For IRRELEVANT documents: relevance_score < 35, key_facts=[], themes=[], "
            "summary explains why it was not used.\n\n"
            "Fields:\n"
            "- summary: 1–3 sentences (what this file adds, or why it was skipped).\n"
            "- key_facts: 0–12 VERBATIM proof points (credentials, deals, metrics, achievements). "
            "Empty if nothing qualifies.\n"
            "- themes: 0–10 lowercase retrieval tags; empty if irrelevant.\n"
            "- doc_type: resume, bio, case_study, deal_sheet, governance, press, other.\n"
            "- relevance_score: number 0–100.\n"
            "- relevance_note: one sentence on fit or why content was excluded.\n"
            "Respond with ONLY JSON."
        )
        user = (
            f"PRINCIPAL PROFILE:\n{principal_blob}\n\n"
            f"FILENAME: {filename}\n\nDOCUMENT TEXT:\n{text[:20000]}"
        )
        data = self._complete_json(system, user)
        if not data:
            return self._fallback.index_document(
                text=text, filename=filename, principal=principal
            )
        score = _as_float(data.get("relevance_score"), 50.0)
        return DocumentIndex(
            summary=(data.get("summary") or "").strip(),
            key_facts=_as_list(data.get("key_facts")),
            themes=[str(t).strip().lower() for t in _as_list(data.get("themes"))],
            doc_type=data.get("doc_type"),
            relevance_score=score,
            relevance_note=(data.get("relevance_note") or "").strip() or None,
            generated_by=f"{self.name}:{self.model}",
        )

    def score_relevance(
        self,
        *,
        principal: dict,
        organization: Optional[dict] = None,
        person: Optional[dict] = None,
    ) -> InsightResult:
        if not self.api_key:
            return self._fallback.score_relevance(
                principal=principal, organization=organization, person=person
            )

        linkedin = (person or {}).get("linkedin_url")
        user = (
            "Research this prospect and brief the principal.\n\n"
            "PRINCIPAL (who wants the intro):\n"
            + json.dumps(principal, indent=2, default=str)
            + "\n\nPROSPECT'S ORGANIZATION (from CRM/Apollo — may be wrong):\n"
            + json.dumps(organization or {}, indent=2, default=str)
            + "\n\nPROSPECT (the person to research):\n"
            + json.dumps(person or {}, indent=2, default=str)
            + "\n\n"
            + (
                f"IMPORTANT: Start by opening this LinkedIn profile: {linkedin}\n"
                "Describe THIS person. If their LinkedIn employer/location conflicts "
                "with PROSPECT'S ORGANIZATION above, trust LinkedIn and flag the mismatch.\n\n"
                if linkedin
                else "Note: find this person on LinkedIn via web_search using name, "
                "title, and employer. If you find a matching profile, set "
                "identity_verified=true and return linkedin_url. Sparse CRM org "
                "fields are normal.\n\n"
            )
            + "Return ONLY a JSON object with keys: "
            "relevance_score, snapshot, key_facts (array of {text, source_url, source_title, source_date}), "
            "fit (array of strings with **bold** emphasis), talking_points, linkedin_url, "
            "identity_verified (boolean), identity_warnings (array of strings), "
            "opportunity_type (advisory, board, consulting, investment, acquisition, "
            "partnership, executive_role, networking)."
        )
        # Try the live research a few times: the model occasionally returns
        # prose/empty (no parseable JSON) even when the API call succeeds. A naive
        # single attempt then silently produces a misleading stub score.
        data = None
        for attempt in range(3):
            data = self._research_json(_INSIGHT_SYSTEM, user)
            if data:
                break
            logger.warning(
                "Research returned no parseable JSON (attempt %d/3); retrying", attempt + 1
            )
            time.sleep(1.5 * (attempt + 1))
        if not data:
            result = self._fallback.score_relevance(
                principal=principal, organization=organization, person=person
            )
            result.generated_by = f"{self.name} (stub fallback)"
            mark_using_stub("anthropic", reason="Anthropic research failed; using stub insight")
            return result

        return _build_insight_result(
            data, person=person, generated_by=f"{self.name}:{self.model}"
        )

    def generate_outreach(
        self,
        *,
        principal: dict,
        person: Optional[dict] = None,
        organization: Optional[dict] = None,
        insight: Optional[dict] = None,
        style: Optional[dict] = None,
    ) -> OutreachResult:
        if not self.api_key:
            return self._fallback.generate_outreach(
                principal=principal, person=person, organization=organization,
                insight=insight, style=style,
            )

        style_block = ""
        if style:
            style_block = (
                "\n\nCOPY DIRECTIVE (A/B variant — follow this writing approach):\n"
                + json.dumps(style, indent=2, default=str)
                + "\nApply the directive's hook, structure, cta, tone, and length. "
                "Keep it honest, first-person, and on-objective."
            )
        user = (
            "PRINCIPAL:\n"
            + json.dumps(principal, indent=2, default=str)
            + "\n\nPERSON (recipient):\n"
            + json.dumps(person or {}, indent=2, default=str)
            + "\n\nORGANIZATION:\n"
            + json.dumps(organization or {}, indent=2, default=str)
            + "\n\nSTRATEGIC INSIGHT:\n"
            + json.dumps(insight or {}, indent=2, default=str)
            + style_block
            + "\n\nUse credential_summary for at most one line of credibility. "
            "Do not paste proof points verbatim. Do not fabricate.\n"
            "Follow the body structure (greeting, hook, optional bridge, single soft ask). "
            "End on the question. Do NOT add a signature."
            + '\n\nReturn JSON with "subject" and "body".'
        )
        data = self._complete_json(_OUTREACH_SYSTEM, user)
        if not data or not data.get("body"):
            result = self._fallback.generate_outreach(
                principal=principal, person=person, organization=organization,
                insight=insight, style=style,
            )
            result.generated_by = f"{self.name} (stub fallback)"
            mark_using_stub("anthropic", reason="Anthropic outreach draft failed; using stub template")
            return result

        return OutreachResult(
            subject=_clean_outreach_subject(
                data.get("subject"), person=person, organization=organization
            ),
            body=_clean_outreach_body(data["body"]),
            generated_by=f"{self.name}:{self.model}",
        )

    def generate_followup(
        self,
        *,
        principal: dict,
        person: Optional[dict] = None,
        organization: Optional[dict] = None,
        insight: Optional[dict] = None,
        previous: Optional[dict] = None,
        step: int = 2,
    ) -> OutreachResult:
        if not self.api_key:
            return self._fallback.generate_followup(
                principal=principal,
                person=person,
                organization=organization,
                insight=insight,
                previous=previous,
                step=step,
            )

        user = (
            "PRINCIPAL:\n"
            + json.dumps(principal, indent=2, default=str)
            + "\n\nPERSON (recipient):\n"
            + json.dumps(person or {}, indent=2, default=str)
            + "\n\nORGANIZATION:\n"
            + json.dumps(organization or {}, indent=2, default=str)
            + "\n\nPREVIOUS EMAIL (sent, no reply yet):\n"
            + json.dumps(previous or {}, indent=2, default=str)
            + f"\n\nThis is follow-up #{step}. Write a shorter, friendly nudge with ONE "
            "fresh angle. Do not repeat the first email. End on the question, no signature."
            + '\n\nReturn JSON with "body".'
        )
        data = self._complete_json(_FOLLOWUP_SYSTEM, user)
        if not data or not data.get("body"):
            return self._fallback.generate_followup(
                principal=principal,
                person=person,
                organization=organization,
                insight=insight,
                previous=previous,
                step=step,
            )
        return OutreachResult(
            subject="",
            body=_clean_outreach_body(data["body"]),
            generated_by=f"{self.name}:{self.model} followup",
        )

    def generate_reply(
        self,
        *,
        principal: dict,
        person: Optional[dict] = None,
        organization: Optional[dict] = None,
        insight: Optional[dict] = None,
        previous: Optional[dict] = None,
        inbound_reply: Optional[str] = None,
    ) -> OutreachResult:
        if not self.api_key:
            return self._fallback.generate_reply(
                principal=principal,
                person=person,
                organization=organization,
                insight=insight,
                previous=previous,
                inbound_reply=inbound_reply,
            )

        user = (
            "PRINCIPAL:\n"
            + json.dumps(principal, indent=2, default=str)
            + "\n\nPERSON (recipient):\n"
            + json.dumps(person or {}, indent=2, default=str)
            + "\n\nORGANIZATION:\n"
            + json.dumps(organization or {}, indent=2, default=str)
            + "\n\nOUR ORIGINAL EMAIL:\n"
            + json.dumps(previous or {}, indent=2, default=str)
            + "\n\nTHEIR REPLY (what you must respond to):\n"
            + (inbound_reply or "").strip()
            + "\n\nWrite the principal's reply. End on the ask, no signature."
            + '\n\nReturn JSON with "body".'
        )
        data = self._complete_json(_REPLY_SYSTEM, user)
        if not data or not data.get("body"):
            return self._fallback.generate_reply(
                principal=principal,
                person=person,
                organization=organization,
                insight=insight,
                previous=previous,
                inbound_reply=inbound_reply,
            )
        return OutreachResult(
            subject="",
            body=_clean_outreach_body(data["body"]),
            generated_by=f"{self.name}:{self.model} reply",
        )


def _is_transient_error(exc: Exception) -> bool:
    """True for rate-limit / overloaded / timeout errors worth retrying."""
    status = getattr(exc, "status_code", None)
    if status in (408, 429, 500, 502, 503, 529):
        return True
    text = str(exc).lower()
    return any(
        kw in text
        for kw in ("rate limit", "overloaded", "429", "529", "timeout", "timed out")
    )


def _linkedin_slug(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    match = re.search(r"linkedin\.com/in/([^/?#]+)", url, re.IGNORECASE)
    return match.group(1).lower() if match else None


_HARD_IDENTITY_PHRASES = (
    "employer differ",
    "different employer",
    "wrong company",
    "wrong country",
    "does not work at",
    "not employed at",
    "conflicting employer",
    "linkedin employer differs",
    "crm employer differs",
    "different person",
    "not the same person",
)

_SOFT_IDENTITY_PHRASES = (
    "sparse",
    "no domain",
    "common name",
    "multiple people named",
    "cannot be confirmed with certainty",
    "plausible",
    "likely the correct",
    "not fully verified",
    "no linkedin url was provided",
    "unable to cross-check",
    "crm org record",
)


def _identity_confidence(
    *,
    warnings: list[str],
    identity_verified: bool,
    linkedin_url: Optional[str],
    person: Optional[dict],
) -> str:
    """verified | likely | uncertain | conflict — drives score caps."""
    warnings_lower = [w.lower() for w in warnings]

    provided_slug = _linkedin_slug((person or {}).get("linkedin_url"))
    returned_slug = _linkedin_slug(linkedin_url)
    if provided_slug and returned_slug and provided_slug != returned_slug:
        return "conflict"

    if any(
        any(phrase in w for phrase in _HARD_IDENTITY_PHRASES) for w in warnings_lower
    ):
        return "conflict"

    if identity_verified:
        return "verified"

    if linkedin_url and (
        not warnings
        or all(
            any(soft in w for soft in _SOFT_IDENTITY_PHRASES) for w in warnings_lower
        )
        or any("matches" in w for w in warnings_lower)
    ):
        return "likely"

    if linkedin_url:
        return "uncertain"

    return "uncertain"


def _apply_relevance_score_cap(
    score: float,
    confidence: str,
    fit: list[str],
) -> float:
    if confidence == "conflict":
        return min(score, 35.0)
    if confidence == "uncertain":
        return min(score, 60.0)
    if any("weak fit" in f.lower() or "sector mismatch" in f.lower() for f in fit):
        return min(score, 45.0)
    return score


def _build_insight_result(
    data: dict, *, person: Optional[dict] = None, generated_by: str = "anthropic"
) -> InsightResult:
    """Normalize model output and apply honest scoring when identity is shaky."""
    fit = _as_list(data.get("fit"))
    why_relevant = data.get("why_relevant") or ("\n".join(fit) if fit else None)
    snapshot = (data.get("snapshot") or "").strip()
    key_facts = _as_sourced_bullets(data.get("key_facts"))
    warnings = _as_list(data.get("identity_warnings"))
    identity_verified = bool(data.get("identity_verified", True))

    linkedin_url = data.get("linkedin_url") or (person or {}).get("linkedin_url")

    confidence = _identity_confidence(
        warnings=warnings,
        identity_verified=identity_verified,
        linkedin_url=linkedin_url,
        person=person,
    )
    if confidence in ("verified", "likely"):
        identity_verified = True

    score = _as_float(data.get("relevance_score"), 60.0)

    # NOTE: We intentionally do NOT raise the LLM's score to match the discovery
    # title-match score. The discovery score is a coarse title prefilter (e.g.
    # any "Partner" title scores ~94), while the LLM score reflects real,
    # web-grounded relevance to the outreach goal. Letting the title-match force
    # the score up produced "everyone is 92" — wrong-domain people ranked as top
    # fits despite the research itself saying "skip". The researched score wins.
    score = _apply_relevance_score_cap(score, confidence, fit)

    if confidence == "conflict":
        identity_verified = False

    if not identity_verified and confidence == "uncertain" and not warnings:
        warnings.append("Person identity could not be fully verified against CRM data.")

    return InsightResult(
        relevance_score=score,
        why_relevant=why_relevant,
        snapshot=snapshot or None,
        key_facts=key_facts,
        sources=data.get("sources") if isinstance(data.get("sources"), list) else [],
        linkedin_url=linkedin_url,
        signals=_as_list(data.get("signals")),
        talking_points=_as_list(data.get("talking_points")),
        opportunity_type=data.get("opportunity_type"),
        generated_by=generated_by,
        identity_verified=identity_verified,
        identity_warnings=warnings,
    )


def _parse_json_value(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _parse_json(text: str) -> Optional[dict]:
    data = _parse_json_value(text)
    return data if isinstance(data, dict) else None


def _index_batch_items(data: Any) -> dict[int, dict]:
    items = data if isinstance(data, list) else (data or {}).get("emails") if isinstance(data, dict) else None
    by_index: dict[int, dict] = {}
    if not isinstance(items, list):
        return by_index
    for item in items:
        if isinstance(item, dict) and "i" in item:
            try:
                by_index[int(item["i"])] = item
            except (TypeError, ValueError):
                continue
    return by_index


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return [str(v) for v in value]
    if value:
        return [str(value)]
    return []


def _as_sourced_bullets(value: Any) -> list:
    """Normalize key_facts to [{text, source_url?, source_title?}, ...]."""
    if not value:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            text = (item.get("text") or "").strip()
            if not text:
                continue
            url = item.get("source_url") or item.get("url")
            title = item.get("source_title") or item.get("title")
            date = item.get("source_date") or item.get("date")
            out.append(
                {
                    "text": text,
                    "source_url": str(url).strip() if url else None,
                    "source_title": str(title).strip() if title else None,
                    "source_date": str(date).strip() if date else None,
                }
            )
        else:
            text = str(item).strip()
            if text:
                out.append(
                    {
                        "text": text,
                        "source_url": None,
                        "source_title": None,
                        "source_date": None,
                    }
                )
    return out


_BANNED_OPENERS = (
    "i hope this finds you well",
    "i hope this email finds you well",
    "hope you are well",
    "hope you're well",
    "i wanted to reach out",
    "i am reaching out",
    "i'm reaching out",
    "my name is",
)

_GENERIC_SUBJECTS = {
    "",
    "introduction",
    "intro",
    "reaching out",
    "opportunity",
    "quick note",
    "quick question",
    "connecting",
    "hello",
    "hi",
}


def _first_name_of(d: Optional[dict]) -> str:
    name = ((d or {}).get("name") or "").strip()
    return name.split()[0] if name else ""


_BANNED_PHRASES = (
    "board seat",
    "independent board",
    "independent director",
    "exploring independent",
    "looking for a board",
    "i have been tracking how",
    "target roles identified",
    "i am a cfa/cpa healthcare operator (",
    "exploring independent board seats",
)


def _clean_outreach_body(body: str) -> str:
    """Enforce short, dash-free outreach copy and strip filler openers."""
    text = (body or "").replace("—", ", ").replace("–", ", ").replace(" - ", ", ")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    cleaned: list[str] = []
    for ln in lines:
        low = ln.lower().rstrip(".,!")
        if any(low.startswith(b) for b in _BANNED_OPENERS):
            continue
        if any(phrase in low for phrase in _BANNED_PHRASES):
            continue
        cleaned.append(ln)
    return "\n".join(cleaned[:6])


def _clean_outreach_subject(
    subject: Optional[str],
    *,
    person: Optional[dict] = None,
    organization: Optional[dict] = None,
) -> str:
    """Normalize the subject and replace weak/generic ones with a specific fallback."""
    text = (subject or "").replace("—", " ").replace("–", " ").replace("!", "")
    text = text.strip().strip('"').strip("'")
    for prefix in ("subject:", "re:", "fwd:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):].strip()
    # Reject generic/empty subjects in favor of something specific.
    if text.lower() in _GENERIC_SUBJECTS:
        org = ((organization or {}).get("name") or "").strip()
        first = _first_name_of(person)
        if org:
            text = f"{org} and boards"
        elif first:
            text = f"A note for {first}"
        else:
            text = "Board experience"
    return text[:70].strip()
