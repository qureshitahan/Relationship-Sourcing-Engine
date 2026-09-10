"""Classic Search LinkedIn: HTTP surface for the search-sourced outreach tab.

A separate router from ``/linkedin`` and ``/linkedin-followers`` on purpose. It
reuses that module's account picker (``/linkedin/accounts``,
``/linkedin/select-account``) so "which account am I acting as" has one answer
app-wide, and it reuses the same provider, the same ``LinkedInMessage`` table and
the same per-account daily cap — but its own audience (LinkedIn's people search),
its own checkpoint and its own progress record.

Long-running work (search, draft, send) runs on a background thread and reports
through ``GET /progress``. Sending is one or two provider calls per lead, paced
seconds apart, which outlives both the browser's timeout and the gateway's.
Drafting is pure string formatting (no model call) so it is fast, but it stays on
the same background path for a consistent progress UI over thousands of leads.

Nothing here runs on its own. Every one of search / draft / approve / send is a
button the user presses.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.enums import AuditAction, LinkedInStatus
from app.models.linkedin_message import LinkedInMessage
from app.models.linkedin_search_lead import (
    LinkedInSearchLead,
    LinkedInSearchSend,
    SearchSendStatus,
)
from app.schemas.entities import Page
from app.schemas.requests import (
    SearchActionRequest,
    SearchDraftRequest,
    SearchFilters,
    SearchRunRequest,
)
from app.services import linkedin_search as service
from app.services.audit import log_action
from app.services.linkedin_providers import get_linkedin_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/linkedin-search", tags=["linkedin-search"])


class SearchLeadOut(BaseModel):
    """One lead plus its outreach state for the requested message."""

    id: int
    account_id: str
    provider_id: str
    name: Optional[str] = None
    headline: Optional[str] = None
    location: Optional[str] = None
    company: Optional[str] = None
    job_title: Optional[str] = None
    network_distance: Optional[str] = None
    profile_url: Optional[str] = None
    picture_url: Optional[str] = None
    # Message state for this campaign (None = not drafted yet).
    message_id: Optional[int] = None
    message_status: Optional[str] = None
    body: Optional[str] = None
    # Checkpoint state for this campaign (None = never attempted).
    send_status: Optional[str] = None
    reach: Optional[str] = None
    sent_at: Optional[str] = None
    error: Optional[str] = None


def _resolve_account(account_id: Optional[str]) -> str:
    resolved = (account_id or "").strip() or service.active_account_id()
    if not resolved:
        raise HTTPException(
            status_code=400,
            detail="No LinkedIn account is selected. Connect one and pick it first.",
        )
    return resolved


def _filters_dict(filters: Optional[SearchFilters]) -> dict:
    """Only the boxes the user actually filled in.

    A blank box must mean "no filter", never "match nothing", so empties are
    dropped rather than sent as nulls — LinkedIn rejects the whole request for an
    unknown-shaped value.
    """
    if filters is None:
        return {}
    raw = filters.model_dump(exclude_none=True)
    # One title box and a list of them are the same filter; fold them into the
    # list so everything downstream — including the search key — sees one shape.
    titles = [
        title.strip()
        for title in ([raw.pop("job_title", None)] + list(raw.pop("job_titles", []) or []))
        if isinstance(title, str) and title.strip()
    ]
    cleaned = {key: value for key, value in raw.items() if value not in ("", [], {})}
    if titles:
        # Deduplicated but kept in the order they were typed, so the same titles
        # entered twice do not make a different search.
        cleaned["job_titles"] = list(dict.fromkeys(titles))
    return cleaned


#: LinkedIn's own seniority ids. The buttons show human labels; the search only
#: accepts these exact strings, and silently rejects the whole request otherwise.
_SENIORITY_IDS = {
    "Owner": "owner/partner",
    "Partner": "owner/partner",
    "CXO": "cxo",
    "Vice President": "vice_president",
    "Director": "director",
    "Manager": "experienced_manager",
    "Senior": "senior",
    "Entry": "entry_level",
}

#: Headcount arrives from the UI as a band label; LinkedIn wants a {min, max}
#: pair, and only these exact numbers.
_HEADCOUNT_BANDS = {
    "1-10": {"min": 1, "max": 10},
    "11-50": {"min": 11, "max": 50},
    "51-200": {"min": 51, "max": 200},
    "201-500": {"min": 201, "max": 500},
    "501-1000": {"min": 501, "max": 1000},
    "1001-5000": {"min": 1001, "max": 5000},
    "5001-10000": {"min": 5001, "max": 10000},
    "10001+": {"min": 10001},
}


def _provider_filters(filters: dict, api: str) -> dict:
    """Translate our field names into the shapes LinkedIn's search demands.

    The two APIs are NOT the same request with a different flag — they take
    different property shapes and different id namespaces, and Unipile rejects
    the entire call (400 invalid_parameters) rather than ignoring a field it does
    not recognise. Learned from a live 400 on the first real search:

    * Sales Navigator wraps ``role``, ``seniority``, ``location`` and
      ``industry`` in ``{"include": [...]}``; classic takes bare arrays and has
      no seniority or headcount filter at all.
    * Classic has no job-title field either; the title goes in
      ``advanced_keywords.title``.
    * ``company_headcount`` is a list of ``{min, max}`` objects, not band labels.
    * The ids differ per API too: classic resolves LOCATION / INDUSTRY, Sales
      Navigator resolves REGION / SALES_INDUSTRY. The page asks for the right
      type, so ids from one mode must not be reused in the other.
    """
    sales = api == "sales_navigator"
    out: dict = {}
    if filters.get("keywords"):
        out["keywords"] = filters["keywords"]

    titles = [t for t in (filters.get("job_titles") or []) if t]
    if titles:
        if sales:
            # ``role.include`` is a list and accepts plain text, so several
            # titles are one search rather than one search each.
            out["role"] = {"include": titles}
        else:
            # Classic takes a single title STRING, so several become LinkedIn's
            # own OR syntax — the same thing you would type into its search box.
            out["advanced_keywords"] = {
                "title": titles[0]
                if len(titles) == 1
                else " OR ".join(f'"{t}"' for t in titles)
            }

    if filters.get("network_distance"):
        out["network_distance"] = [int(d) for d in filters["network_distance"]]

    if filters.get("location"):
        out["location"] = (
            {"include": list(filters["location"])} if sales else list(filters["location"])
        )
    if filters.get("industry"):
        out["industry"] = (
            {"include": list(filters["industry"])} if sales else list(filters["industry"])
        )

    # Classic search has neither of these on LinkedIn's side, so they are dropped
    # rather than sent — sending them is what returns 400 and finds nobody.
    if sales:
        if filters.get("seniority"):
            ids = [
                _SENIORITY_IDS[label]
                for label in filters["seniority"]
                if label in _SENIORITY_IDS
            ]
            if ids:
                out["seniority"] = {"include": sorted(set(ids))}
        if filters.get("company_headcount"):
            bands = [
                _HEADCOUNT_BANDS[band]
                for band in filters["company_headcount"]
                if band in _HEADCOUNT_BANDS
            ]
            if bands:
                out["company_headcount"] = bands
    return {key: value for key, value in out.items() if value not in (None, "", [], {})}


def _resolve_campaign(message: str) -> str:
    key = service.campaign_key_for(message)
    if not key:
        raise HTTPException(
            status_code=400,
            detail="A message is required — it is the text that gets sent.",
        )
    return key


def _resolve_search(filters: dict) -> str:
    key = service.search_key_for(filters)
    if not key:
        raise HTTPException(
            status_code=400,
            detail="Fill in at least one search filter before searching.",
        )
    return key


@router.get("/status")
def search_status(
    db: Session = Depends(get_db),
    message: Optional[str] = None,
    account_id: Optional[str] = None,
    search_key: Optional[str] = None,
):
    """Connection status + lead/campaign counts for the page header.

    Safe to call with no message and no account: it reports what is missing
    instead of failing, so the page can render its own setup state.
    """
    provider = get_linkedin_provider(account_id or None)
    lister = getattr(provider, "list_accounts", None)
    accounts = lister() if lister else []
    account_id = (account_id or "").strip() or service.active_account_id()
    active = next((a for a in accounts if a.get("id") == account_id), None)

    payload: dict = {
        "provider": provider.name,
        "configured": provider.name == "stub"
        or bool(settings.unipile_api_key and settings.unipile_dsn),
        "supports_search": provider.supports_search(),
        "active_account_id": account_id,
        "active_account_name": (active or {}).get("name"),
        "active_account_status": (active or {}).get("status"),
        "accounts": accounts,
        "campaign_key": None,
        "stats": None,
        # Served rather than hard-coded in the page. The invitation note is
        # truncated to this server-side, and a page that promised a different
        # number would silently swallow the tail of what was typed.
        "invite_note_max_chars": int(settings.linkedin_invite_note_max_chars),
    }
    if not account_id:
        return payload

    key = service.campaign_key_for(message)
    if key:
        payload["campaign_key"] = key
        payload["stats"] = service.campaign_stats(
            db, account_id=account_id, search_key=search_key, campaign_key=key
        )
    return payload


@router.get("/progress")
def search_progress():
    """Live state of the running search/draft/send job (poll this for the bar)."""
    state = service.read_progress()
    # A job whose process died leaves "running" in the row for good. Report that
    # honestly here — this is what the bar and every disabled button read —
    # rather than in read_progress(), so a live worker's writes are never
    # rewritten underneath it.
    return service.stale_progress(state) or state


@router.get("/parameters")
def search_parameters(
    kind: str = Query(..., description="LOCATION | INDUSTRY | COMPANY | SCHOOL"),
    keywords: str = Query(..., min_length=1),
    account_id: Optional[str] = None,
):
    """Type-ahead for the filters LinkedIn takes as ids rather than free text.

    Without this, a location or industry box would look like it worked and then
    quietly match nothing, because LinkedIn ignores an unresolvable value.
    """
    resolved = _resolve_account(account_id)
    provider = get_linkedin_provider(resolved)
    options = provider.search_parameters(kind=kind, keywords=keywords)
    return {"items": [{"id": o.id, "title": o.title} for o in options]}


@router.post("/run")
def run_search(payload: SearchRunRequest, db: Session = Depends(get_db)):
    """Run the search in the background and store what it finds as leads."""
    account_id = _resolve_account(payload.account_id)
    provider = get_linkedin_provider(account_id)
    if not provider.supports_search():
        raise HTTPException(
            status_code=400,
            detail="This LinkedIn provider cannot search. "
            "Set LINKEDIN_PROVIDER=unipile with a connected account.",
        )
    filters = _filters_dict(payload.filters)
    search_key = _resolve_search(filters)
    if not service.launch_search(
        account_id=account_id,
        filters=_provider_filters(filters, payload.api),
        api=payload.api,
        search_key=search_key,
        pages=payload.pages,
    ):
        return {"started": False, "message": "A search job is already running."}
    log_action(
        db,
        AuditAction.LINKEDIN_SEND,
        entity_type="linkedin_search",
        actor="human",
        summary=f"Ran a LinkedIn {payload.api} search",
        commit=True,
    )
    return {
        "started": True,
        "account_id": account_id,
        "search_key": search_key,
        "message": "Searching LinkedIn in the background.",
    }


@router.post("/draft-all")
def draft_all(payload: SearchDraftRequest, db: Session = Depends(get_db)):
    """Draft a message for every lead of this search not yet drafted for it."""
    account_id = _resolve_account(payload.account_id)
    filters = _filters_dict(payload.filters)
    search_key = _resolve_search(filters)
    campaign_key = _resolve_campaign(payload.message)

    limit = payload.limit if (payload.limit or 0) > 0 else None
    # A target names how many the campaign should END UP with, so pressing the
    # button again tops up rather than doubling. Counted in PEOPLE, because a
    # lead can hold more than one message row and a row count would make the
    # target go dead early.
    if (payload.target or 0) > 0:
        existing = int(
            db.execute(
                select(func.count(func.distinct(LinkedInMessage.search_lead_id))).where(
                    LinkedInMessage.search_lead_id.is_not(None),
                    LinkedInMessage.search_campaign_key == campaign_key,
                    service.account_lead_filter(account_id, search_key),
                )
            ).scalar_one()
        )
        needed = max(0, int(payload.target) - existing)
        if needed == 0:
            return {
                "started": False,
                "candidates": 0,
                "search_key": search_key,
                "campaign_key": campaign_key,
                "message": f"Already {existing} drafted for this message — "
                "raise the number, or use Append to add more.",
            }
        limit = needed

    eligible = service.eligible_leads(
        db,
        account_id=account_id,
        search_key=search_key,
        campaign_key=campaign_key,
        limit=limit,
    )
    if not eligible:
        # "Everyone is drafted" and "nobody has been searched for yet" both
        # surface as an empty candidate list, and the first wording sends people
        # hunting for drafts that were never possible.
        found = int(
            db.execute(
                select(func.count())
                .select_from(LinkedInSearchLead)
                .where(
                    LinkedInSearchLead.account_id == account_id,
                    LinkedInSearchLead.search_key == search_key,
                )
            ).scalar_one()
        )
        return {
            "started": False,
            "candidates": 0,
            "search_key": search_key,
            "campaign_key": campaign_key,
            "message": (
                'No results for these filters yet — press "Search LinkedIn" first.'
                if found == 0
                else "Every result already has a message for this text — "
                'search again to pick up new people.'
            ),
        }
    if not service.launch_draft(
        account_id=account_id,
        search_key=search_key,
        campaign_key=campaign_key,
        message=payload.message,
        invitation_note=payload.invitation_note,
        principal_id=payload.principal_id,
        limit=limit,
    ):
        return {
            "started": False,
            "candidates": len(eligible),
            "search_key": search_key,
            "campaign_key": campaign_key,
            "message": "A search job is already running.",
        }
    return {
        "started": True,
        "candidates": len(eligible),
        "search_key": search_key,
        "campaign_key": campaign_key,
        "message": f"Writing {len(eligible)} message(s) in the background.",
    }


@router.post("/approve-all")
def approve_all(payload: SearchActionRequest, db: Session = Depends(get_db)):
    """Approve every drafted message for this search + text (no provider calls)."""
    account_id = _resolve_account(payload.account_id)
    filters = _filters_dict(payload.filters)
    search_key = service.search_key_for(filters)
    campaign_key = _resolve_campaign(payload.message)
    approved = service.approve_all(
        db, account_id=account_id, campaign_key=campaign_key, search_key=search_key
    )
    return {"approved": approved, "campaign_key": campaign_key}


@router.post("/send-all")
def send_all(payload: SearchActionRequest, db: Session = Depends(get_db)):
    """Approve + send every open message for this search, in the background.

    Paced and capped per account exactly like the other LinkedIn send paths, and
    every send passes the checkpoint first, so an already-contacted lead is
    skipped even if this is clicked twice.
    """
    account_id = _resolve_account(payload.account_id)
    filters = _filters_dict(payload.filters)
    search_key = service.search_key_for(filters)
    campaign_key = _resolve_campaign(payload.message)
    # Counted with the SAME rule the send queue applies, and in distinct leads:
    # a duplicate row for one lead is refused by the checkpoint, so counting rows
    # would promise more sends than could ever land.
    open_count = int(
        db.execute(
            select(func.count(func.distinct(LinkedInMessage.search_lead_id)))
            .select_from(LinkedInMessage)
            .where(*service.open_message_conditions(account_id, campaign_key, search_key))
        ).scalar_one()
    )
    if not open_count:
        return {
            "started": False,
            "matched": 0,
            "campaign_key": campaign_key,
            "message": 'Nothing to send — click "Draft all" first.',
        }
    if not service.launch_send(
        account_id=account_id, campaign_key=campaign_key, search_key=search_key
    ):
        return {
            "started": False,
            "matched": open_count,
            "campaign_key": campaign_key,
            "message": "A search job is already running.",
        }
    return {
        "started": True,
        "matched": open_count,
        "campaign_key": campaign_key,
        "message": f"Reaching up to {open_count} person(s) in the background.",
    }


@router.post("/stop")
def stop(db: Session = Depends(get_db)):
    """Halt the running job between items.

    The message in flight finishes, so nothing is left half-sent: its checkpoint
    row is resolved before the worker looks at the stop flag again.
    """
    if not service.request_stop():
        if service.clear_stale_progress():
            return {
                "stopped": True,
                "message": "That job had already stopped when the server "
                "restarted — cleared it. Anything it saved is kept.",
            }
        return {"stopped": False, "message": "No search job is running."}
    log_action(
        db,
        AuditAction.LINKEDIN_SEND,
        entity_type="linkedin_search",
        actor="human",
        summary="Stopped the LinkedIn search job",
        commit=True,
    )
    return {
        "stopped": True,
        "message": "Stopping — the message in flight finishes, then it halts.",
    }


#: How far along a message row is, for picking one row per lead when a lead ended
#: up with several. Defined in the service and reused here so the tab counts and
#: the rows this list returns can never rank a lead differently.
_PROGRESS_RANK = service.PROGRESS_RANK


def _rank(msg: LinkedInMessage) -> tuple[int, int]:
    return (_PROGRESS_RANK.get(msg.status, 0), msg.id or 0)


@router.get("", response_model=Page[SearchLeadOut])
def list_leads(
    db: Session = Depends(get_db),
    message: Optional[str] = None,
    search_key: Optional[str] = None,
    status: Optional[str] = Query(
        None, description="draft | approved | invite_sent | sent | replied | pending"
    ),
    account_id: Optional[str] = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
):
    """Leads of the selected account, joined to their state for this message.

    Everything returned comes from the database, so the tabs read the same after
    a page refresh or a server restart as they did before it.
    """
    resolved_account = _resolve_account(account_id)
    campaign_key = service.campaign_key_for(message)

    messages: dict[int, LinkedInMessage] = {}
    sends: dict[str, LinkedInSearchSend] = {}
    if campaign_key:
        for msg in db.execute(
            select(LinkedInMessage).where(
                LinkedInMessage.search_lead_id.is_not(None),
                LinkedInMessage.search_campaign_key == campaign_key,
                service.account_lead_filter(resolved_account, search_key),
            )
        ).scalars().all():
            if msg.search_lead_id is None:
                continue
            # A lead can hold more than one message for the same text. Keep the
            # furthest-along row: what happened outranks what was merely queued,
            # so someone already contacted cannot show up under Approved.
            current = messages.get(msg.search_lead_id)
            if current is None or _rank(msg) > _rank(current):
                messages[msg.search_lead_id] = msg
        for row in db.execute(
            select(LinkedInSearchSend).where(
                LinkedInSearchSend.account_id == resolved_account,
                LinkedInSearchSend.campaign_key == campaign_key,
            )
        ).scalars().all():
            sends[row.lead_provider_id] = row

    lead_query = select(LinkedInSearchLead).where(
        LinkedInSearchLead.account_id == resolved_account
    )
    if search_key:
        lead_query = lead_query.where(LinkedInSearchLead.search_key == search_key)
    leads = list(db.execute(lead_query.order_by(LinkedInSearchLead.id)).scalars().all())

    settled_ids = {
        pid for pid, row in sends.items() if row.status in SearchSendStatus.SETTLED
    }

    def _matches(lead: LinkedInSearchLead) -> bool:
        if not status:
            return True
        msg = messages.get(lead.id)
        if status == "pending":
            return msg is None
        if msg is None or msg.status != status:
            return False
        # A leftover draft for someone already settled can never be sent, and its
        # tab count no longer includes it, so the list must not show it either.
        if status in (LinkedInStatus.DRAFT, LinkedInStatus.APPROVED):
            return lead.provider_id not in settled_ids
        return True

    filtered = [lead for lead in leads if _matches(lead)]
    window = filtered[offset : offset + limit]

    items: list[SearchLeadOut] = []
    for lead in window:
        msg = messages.get(lead.id)
        send = sends.get(lead.provider_id)
        items.append(
            SearchLeadOut(
                id=lead.id,
                account_id=lead.account_id,
                provider_id=lead.provider_id,
                name=lead.name,
                headline=lead.headline,
                location=lead.location,
                company=lead.company,
                job_title=lead.job_title,
                network_distance=lead.network_distance,
                profile_url=lead.profile_url,
                picture_url=lead.picture_url,
                message_id=msg.id if msg else None,
                message_status=msg.status if msg else None,
                body=msg.body if msg else None,
                send_status=send.status if send else None,
                reach=send.reach if send else None,
                sent_at=send.sent_at.isoformat() if send and send.sent_at else None,
                error=(send.error if send else None) or (msg.error if msg else None),
            )
        )
    return Page[SearchLeadOut](
        items=items, total=len(filtered), limit=limit, offset=offset
    )
