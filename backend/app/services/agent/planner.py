"""Turn a plain-language outreach goal into clarifying questions + Apollo ICP criteria."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional

from app.core.config import settings
from app.models.principal import Principal

logger = logging.getLogger(__name__)

# Default title/seniority sets the UI exposes.
DEFAULT_BOARD_TITLES = [
    "Operating Partner",
    "Talent Partner",
    "Managing Partner",
    "General Partner",
    "Head of Talent",
    "Value Creation Partner",
    "Independent Director",
    "Board Member",
    "Board Chair",
]
DEFAULT_PHARMA_TITLES = [
    "Chief Medical Officer",
    "VP Medical Affairs",
    "Director Medical Affairs",
    "Head of Market Access",
    "VP Market Access",
    "Chief Pharmacy Officer",
]
DEFAULT_HOSPITAL_TITLES = [
    "Chief Medical Officer",
    "Chief Operating Officer",
    "President",
    "CEO",
    "VP Operations",
    "Director Pharmacy",
]

_PLANNER_SYSTEM = """You help configure a B2B outreach search engine powered by Apollo.io People API Search.

Given a principal's objective and optional clarifying answers, output JSON only:
{
  "questions": [...],
  "criteria": {
    "titles": [...],
    "seniorities": [...],
    "geographies": ["United States"],
    "industries": [...],
    "company_types": [...],
    "healthcare_sectors": [],
    "keywords": [],
    "themes": [],
    "organization_job_titles": [...],
    "contact_email_status": [],
    "employee_min": null,
    "employee_max": null,
    "people_limit": 50
  },
  "rationale": "one sentence"
}

CREATIVITY (critical):
- You are NOT limited to any preset list. Invent job titles, keywords, themes, and company-type tags
  that fit the user's objective — think like a researcher who knows the domain.
- For pharma/formulary: invent titles like "Pharmacy & Therapeutics Chair", "Clinical Pharmacy Director",
  "VP Clinical Services", "Medication Management Director" — whatever fits the objective.
- For board seats: think Operating Partner, board search, governance committee chairs, etc.
- For themes: invent acquisition/investment angles relevant to the goal (not just generic "roll-up").
- For industries: use 1-3 BROAD keyword tags; you may use tags not in any preset list.
- For company_types: invent descriptive keyword tags (e.g. "mid-market PE", "regional health system").
- Always explain your title choices in rationale.

Apollo API rules:
- person_seniorities ONLY: owner, founder, c_suite, partner, vp, head, director, manager, senior, entry, intern.
- contact_email_status: verified, unverified, likely to engage — NEVER unavailable.
- person_titles[] accepts ANY free-text job title — be creative and exhaustive for the use case.
- Industries/company_types/keywords/themes → q_organization_keyword_tags (broad tags, 1-3 industries max).
- Geography → person_locations only (where the person lives/works). Default United States.
  Backend expands US/USA synonyms. Add states/cities only when user specifies regional focus.
  Do NOT worry about employer HQ location — we filter by person location, not organization_locations.
- employee_min optional (e.g. 10 to skip micro-companies). Leave employee_max null unless user asks.
- organization_job_titles = active job postings at employer; use when hiring signals matter.
- Clarifying questions (ONLY when clarifying_answers is empty): ask 3-5 SHARP, SPECIFIC
  questions that are clearly grounded in THIS objective — quote the user's own words and
  resolve the real ambiguities, do not ask generic boilerplate. Each question must change
  what you search for. Prioritize, in order:
    (1) The precise outcome/ask (what does a "win" look like — a meeting, a referral, a
        pilot, a board intro?) so the email's call-to-action is right.
    (2) Exactly WHO to target: the specific roles/seniority AND whether to include adjacent
        personas (e.g. portfolio-company operators vs. fund partners) — give a concrete
        recommended default in "suggested".
    (3) The principal's own credibility/angle for this audience (what makes them worth a
        reply), since the email is written in the principal's voice.
    (4) Any must-have filters or exclusions: geography focus, company size/stage, sectors to
        include or avoid.
    (5) Volume: how many new people to surface per run (default 50).
  Write each question so a busy person can answer in a few words; always populate "suggested"
  with the smartest default so they can just confirm.
- When clarifying_answers provided, return questions: [] and finalize criteria.

VOLUME DISCOVERY (critical — discovery must return people_limit prospects):
- This is a BROAD net pass. Relevance filtering happens later on the Prospects page.
- people_limit: use the user's discover_target answer, or default 50. Never set below 20.
- titles: 12–18 broad decision-maker titles — be generous so discovery returns enough people.
  Include the obvious core roles AND close synonyms/adjacent variants (e.g. for board work:
  Operating Partner, Managing Director, Partner, Board Member, Independent Director, Chairman,
  CEO, COO, President). Prefer broad role names over hyper-narrow specialties.
- industries: 1–2 BROAD tags only (e.g. "Hospital & Health Care", "Healthcare"). Never stack 3+ industries.
- company_types: 0–2 tags max, or leave empty — industry + title is enough.
- keywords: leave EMPTY unless the user explicitly named a search term. Never put product codes (503B) here.
- themes: 0–2 strategic angles max, or leave empty. Themes narrow Apollo when combined with industries.
- contact_email_status: ALWAYS [] — never filter by email at discovery time.
- employee_min: null unless user explicitly asked to exclude small companies. NEVER infer "large company" as 1200+.
- employee_max: null unless user asked to cap company size.
- organization_job_titles: optional boost only (0–4 titles). Main search must work without them.
"""


def _parse_json(text: str) -> Optional[dict]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                return None
    return None


def _normalize_questions(questions: Any) -> List[dict]:
    """Coerce planner questions to {id, prompt, suggested} dicts for the API schema."""
    out: List[dict] = []
    for i, q in enumerate(questions or []):
        if isinstance(q, dict):
            prompt = (q.get("prompt") or q.get("question") or "").strip()
            if not prompt:
                continue
            out.append(
                {
                    "id": str(q.get("id") or f"q{i}"),
                    "prompt": prompt,
                    "suggested": q.get("suggested"),
                }
            )
        elif isinstance(q, str) and q.strip():
            out.append({"id": f"q{i}", "prompt": q.strip(), "suggested": None})
    return out


def _merge_criteria(base: dict, overlay: dict) -> dict:
    """Fill empty planner fields from a fallback criteria dict."""
    merged = dict(base)
    for key, val in overlay.items():
        if key not in merged or merged[key] in (None, [], ""):
            merged[key] = val
    return merged


_TITLE_PRIORITY_WORDS = (
    "director",
    "chief",
    "vp",
    "vice president",
    "head",
    "president",
    "manager",
    "officer",
)


def _prioritize_titles(titles: List[str], cap: int) -> List[str]:
    """Keep the broadest decision-maker titles when the planner over-lists."""
    if len(titles) <= cap:
        return titles
    priority: List[str] = []
    rest: List[str] = []
    for title in titles:
        lower = title.lower()
        if any(word in lower for word in _TITLE_PRIORITY_WORDS):
            priority.append(title)
        else:
            rest.append(title)
    return (priority + rest)[:cap]


def _sanitize_planner_criteria(data: dict) -> dict:
    """Strip over-narrow AI defaults so discovery can hit people_limit."""
    out = dict(data)

    out["contact_email_status"] = []

    emin = out.get("employee_min")
    if emin is not None:
        try:
            if int(emin) > 200:
                out["employee_min"] = None
        except (TypeError, ValueError):
            out["employee_min"] = None

    keywords = [str(k).strip() for k in (out.get("keywords") or []) if str(k).strip()]
    out["keywords"] = keywords[:2] if keywords else []

    themes = [str(t).strip() for t in (out.get("themes") or []) if str(t).strip()]
    out["themes"] = themes[:2] if themes else []

    industries = [str(i).strip() for i in (out.get("industries") or []) if str(i).strip()]
    out["industries"] = industries[:3] if industries else []

    company_types = [
        str(c).strip() for c in (out.get("company_types") or []) if str(c).strip()
    ]
    if len(company_types) > 2:
        out["company_types"] = company_types[:2]

    titles = [str(t).strip() for t in (out.get("titles") or []) if str(t).strip()]
    seen_titles: set[str] = set()
    unique_titles: List[str] = []
    for title in titles:
        key = title.lower()
        if key not in seen_titles:
            seen_titles.add(key)
            unique_titles.append(title)
    out["titles"] = _prioritize_titles(unique_titles, 18)

    job_titles = [
        str(j).strip() for j in (out.get("organization_job_titles") or []) if str(j).strip()
    ]
    out["organization_job_titles"] = job_titles[:4] if job_titles else []

    raw_limit = out.get("people_limit") or out.get("discover_target") or 50
    try:
        people_limit = max(20, min(int(raw_limit), 500))
    except (TypeError, ValueError):
        people_limit = 50
    out["people_limit"] = people_limit

    return out


def _detect_use_case(objective: str) -> str:
    lower = objective.lower()
    if any(w in lower for w in ("503b", "503 b", "compounding")):
        return "503b_hospital"
    if any(w in lower for w in ("hospital", "health system", "healthcare provider")):
        return "hospital"
    if any(w in lower for w in ("pharma", "pharmaceutical", "formulary", "pharmacy")):
        return "pharma"
    if any(w in lower for w in ("drug", "medication", "therapeutic")):
        return "pharma"
    if any(w in lower for w in ("board", "director", "private equity", " pe ")):
        return "board"
    return "general"


def _heuristic_plan(
    objective: str,
    principal: Optional[Principal],
    answers: Optional[dict],
) -> dict:
    """Rule-based fallback when Anthropic is unavailable."""
    lower = objective.lower()
    use_case = _detect_use_case(objective)

    if use_case == "503b_hospital":
        titles = [
            "Director of Pharmacy",
            "Chief Pharmacy Officer",
            "VP Pharmacy",
            "Pharmacy Director",
            "Chief Medical Officer",
            "VP Medical Affairs",
            "Director of Oncology",
            "Formulary Manager",
        ]
        industries = ["Hospital & Health Care", "Healthcare"]
        company_types = ["hospital", "health system"]
        keywords = []
        themes = []
        board_jobs = ["Director of Pharmacy", "Pharmacy Manager"]
    elif use_case == "hospital":
        titles = DEFAULT_HOSPITAL_TITLES + [
            "Director of Pharmacy",
            "Chief Pharmacy Officer",
            "Formulary Manager",
        ]
        industries = ["Healthcare", "Hospital & Health Care"]
        company_types = ["operating_company", "health_system", "hospital"]
        keywords = []
        themes = []
        board_jobs = []
    elif use_case == "pharma":
        titles = DEFAULT_PHARMA_TITLES + ["Director of Pharmacy", "P&T Committee"]
        industries = ["Healthcare", "Pharmaceuticals"]
        company_types = ["operating_company", "health_system", "pharma"]
        keywords = []
        themes = ["formulary access", "market access"]
        board_jobs = []
    elif use_case == "board":
        titles = DEFAULT_BOARD_TITLES
        industries = ["Healthcare", "Healthcare Services"]
        company_types = ["private_equity", "operating_company"]
        keywords = []
        themes = []
        board_jobs = [
            "Independent Director",
            "Board Member",
            "Non-Executive Director",
        ]
    else:
        titles = DEFAULT_BOARD_TITLES
        industries = ["Healthcare", "Healthcare Services"]
        company_types = ["private_equity", "operating_company"]
        keywords = []
        themes = []
        board_jobs = []

    geo = (answers or {}).get("geography") or "United States"
    try:
        people_limit = max(20, int((answers or {}).get("discover_target") or 50))
    except (TypeError, ValueError):
        people_limit = 50

    criteria = _sanitize_planner_criteria(
        {
            "titles": titles,
            "seniorities": ["c_suite", "vp", "director", "head", "manager"],
            "geographies": [geo],
            "industries": industries,
            "company_types": company_types,
            "healthcare_sectors": [],
            "keywords": keywords,
            "themes": themes,
            "organization_job_titles": board_jobs,
            "employee_min": None,
            "employee_max": None,
            "people_limit": people_limit,
            "contact_email_status": [],
        }
    )

    if answers:
        return {
            "questions": [],
            "criteria": criteria,
            "rationale": "Search plan from your objective (rule-based fallback).",
        }

    focus_suggested = (
        "Mid-market healthcare private equity firms"
        if "private_equity" in company_types
        else "Hospital and health-system pharmacy leaders"
        if use_case in ("503b_hospital", "hospital")
        else "Healthcare operating companies"
    )
    questions = [
        {
            "id": "goal",
            "prompt": "What is the main goal of this outreach? (e.g. sell a product, book a meeting, intro)",
            "suggested": objective[:120] if objective else "",
        },
        {
            "id": "geography",
            "prompt": "Which geography should we search?",
            "suggested": "United States",
        },
        {
            "id": "company_focus",
            "prompt": "What type or size of companies should we prioritize?",
            "suggested": focus_suggested,
        },
        {
            "id": "seniority",
            "prompt": "What seniority level should we target?",
            "suggested": "Director and VP level decision makers",
        },
        {
            "id": "discover_target",
            "prompt": "How many new people should the agent discover per day?",
            "suggested": "60",
        },
    ]
    return {
        "questions": questions,
        "criteria": criteria,
        "rationale": "Draft search plan from your objective — answer the questions and click Plan again to finalize.",
    }


def plan_agent_search(
    *,
    objective_prompt: str,
    principal: Optional[Principal] = None,
    clarifying_answers: Optional[dict] = None,
) -> dict[str, Any]:
    """Return clarifying questions and/or finalized Apollo search criteria."""
    if not objective_prompt.strip():
        raise ValueError("Describe your outreach goal in a few lines first.")

    if settings.anthropic_api_key:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            user_parts = [
                f"OBJECTIVE:\n{objective_prompt.strip()}",
            ]
            if principal:
                user_parts.append(
                    f"PRINCIPAL:\n{json.dumps({'name': principal.name, 'document_focus': principal.document_focus, 'target_sectors': principal.target_sectors}, default=str)}"
                )
            if clarifying_answers:
                user_parts.append(f"CLARIFYING_ANSWERS:\n{json.dumps(clarifying_answers)}")
            else:
                user_parts.append("CLARIFYING_ANSWERS: (none yet — include 4-5 questions)")

            resp = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=1500,
                system=_PLANNER_SYSTEM,
                messages=[{"role": "user", "content": "\n\n".join(user_parts)}],
            )
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", None) == "text"
            )
            parsed = _parse_json(text)
            if parsed and "criteria" in parsed:
                parsed["questions"] = _normalize_questions(parsed.get("questions"))
                crit = parsed.get("criteria") or {}
                if not (crit.get("titles") or crit.get("industries")):
                    fallback = _heuristic_plan(objective_prompt, principal, clarifying_answers)
                    parsed["criteria"] = _merge_criteria(
                        fallback.get("criteria") or {}, crit
                    )
                parsed["criteria"] = _sanitize_planner_criteria(parsed.get("criteria") or {})
                return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("Agent planner AI failed, using heuristic: %s", exc)

    result = _heuristic_plan(objective_prompt, principal, clarifying_answers)
    result["questions"] = _normalize_questions(result.get("questions"))
    if result.get("criteria"):
        result["criteria"] = _sanitize_planner_criteria(result["criteria"])
    return result


def criteria_from_dict(data: dict) -> dict:
    """Normalize a criteria dict from the planner / playbook."""
    geo = data.get("geographies") or ["United States"]
    if isinstance(geo, str):
        geo = [geo]
    normalized = _sanitize_planner_criteria(
        {
            "titles": list(data.get("titles") or []),
            "seniorities": list(data.get("seniorities") or []),
            "contact_email_status": list(data.get("contact_email_status") or []),
            "organization_domains": list(data.get("organization_domains") or []),
            "geographies": geo,
            "industries": list(data.get("industries") or []),
            "company_types": list(data.get("company_types") or []),
            "healthcare_sectors": list(data.get("healthcare_sectors") or []),
            "keywords": list(data.get("keywords") or []),
            "themes": list(data.get("themes") or []),
            "organization_job_titles": list(data.get("organization_job_titles") or []),
            "employee_min": data.get("employee_min"),
            "employee_max": data.get("employee_max"),
            "people_limit": data.get("people_limit") or data.get("discover_target") or 50,
        }
    )
    normalized["organization_domains"] = list(data.get("organization_domains") or [])
    normalized["healthcare_sectors"] = list(data.get("healthcare_sectors") or [])
    normalized["seniorities"] = list(data.get("seniorities") or [])
    return normalized
