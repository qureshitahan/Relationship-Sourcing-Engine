"""Apollo-driven ICP discovery: run discovery and list past runs."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.contact import Contact
from app.models.discovery_run import DiscoveryRun
from app.models.enums import DiscoveryStatus
from app.models.principal import Principal
from app.models.search_definition import SearchDefinition
from app.schemas.entities import DiscoveryRunOut, Page
from app.schemas.requests import DiscoveryRunRequest
from app.services.discovery import delete_discovery_run, run_discovery
from app.services.discovery.relationship_discovery import _criteria_to_dict
from app.services.discovery.process_run import process_discovery_run
from app.schemas.entities import DiscoveryProcessSummary
from app.services.discovery_jobs import (
    JOB_RUNNING,
    launch_discovery,
    launch_run_approve,
    launch_run_draft,
    launch_run_email_send,
    launch_run_linkedin_draft,
    launch_run_linkedin_send,
    launch_run_pipeline,
    launch_run_reveal,
)
from app.services.enrichment import get_discovery_provider
from app.services.enrichment.base import DiscoveryCriteria
from app.services.provider_health import active_warnings

router = APIRouter(prefix="/discovery", tags=["discovery"])


def _default_employee_max(value: Optional[int]) -> Optional[int]:
    """Fall back to a mid-market headcount ceiling when none is given.

    Without an upper bound, the Apollo org search matches Fortune-class brands on
    a single loose keyword tag (e.g. Amazon for "Healthcare Services"), and those
    mega-corps yield low-quality "Board Member" people records. A default cap keeps
    the ICP in the mid-market. ``discovery_employee_max_default = 0`` disables it.
    """
    if value is not None:
        return value
    default = settings.discovery_employee_max_default
    return default if default and default > 0 else None


def _criteria_from_request(
    payload: DiscoveryRunRequest, definition: Optional[SearchDefinition]
) -> DiscoveryCriteria:
    org_limit = payload.org_limit or settings.discovery_org_limit
    people_limit = payload.people_limit or settings.discovery_people_limit
    employee_max = payload.employee_max
    if employee_max is None:
        employee_max = _default_employee_max(None)
    if definition is not None:
        return DiscoveryCriteria(
            industries=definition.industries,
            company_types=definition.company_types,
            healthcare_sectors=definition.healthcare_sectors,
            geographies=definition.geographies,
            titles=definition.titles,
            seniorities=definition.seniorities,
            contact_email_status=payload.contact_email_status,
            organization_domains=payload.organization_domains,
            keywords=definition.keywords,
            themes=definition.themes,
            employee_min=definition.employee_min,
            employee_max=definition.employee_max,
            org_limit=org_limit,
            people_limit=people_limit,
            organization_job_titles=payload.organization_job_titles,
        )
    return DiscoveryCriteria(
        industries=payload.industries,
        company_types=payload.company_types,
        healthcare_sectors=payload.healthcare_sectors,
        geographies=payload.geographies,
        titles=payload.titles,
        seniorities=payload.seniorities,
        contact_email_status=payload.contact_email_status,
        organization_domains=payload.organization_domains,
        keywords=payload.keywords,
        themes=payload.themes,
        employee_min=payload.employee_min,
        employee_max=employee_max,
        org_limit=org_limit,
        people_limit=people_limit,
        organization_job_titles=payload.organization_job_titles,
    )


def _researched_counts(db: Session, run_ids: list[int]) -> dict[int, int]:
    """How many prospects of each run actually have research, counted live.

    ``DiscoveryRun.insights_generated`` is only written while discovery itself
    runs, so research done afterwards — the normal path, since Approve triggers
    it — never reaches the stored counter. That left every such run reporting
    "0 researched" while its prospects were plainly researched and draftable.

    One grouped query for the whole page, so listing runs stays a fixed number
    of queries rather than one per run.
    """
    if not run_ids:
        return {}
    rows = db.execute(
        select(Contact.discovery_run_id, func.count(Contact.id))
        .where(
            Contact.discovery_run_id.in_(run_ids),
            Contact.relevance_score.isnot(None),
        )
        .group_by(Contact.discovery_run_id)
    ).all()
    return {rid: int(count or 0) for rid, count in rows}


def _discovery_run_out(
    run: DiscoveryRun, *, researched: Optional[int] = None
) -> DiscoveryRunOut:
    data = DiscoveryRunOut.model_validate(run)
    data.provider_warnings = active_warnings()
    if researched is not None:
        # Live count beats the stored counter, which only sees discovery-time
        # research. Never report fewer than the run recorded for itself.
        data.insights_generated = max(researched, run.insights_generated or 0)
    return data


@router.post("/run", response_model=DiscoveryRunOut, status_code=202)
def run(payload: DiscoveryRunRequest, db: Session = Depends(get_db)):
    """Kick off an ICP discovery run in the background and return immediately.

    Discovery (Apollo search + import + optional research/reveal) can take many
    minutes for a large ``people_limit``, so it runs in a daemon thread instead of
    holding the HTTP request open until the browser times out. The response is the
    freshly-created run in ``pending``/``running`` state; poll ``GET
    /discovery/runs/{id}`` until ``status`` is ``completed`` or ``failed``.
    """
    principal = db.get(Principal, payload.principal_id)
    if not principal:
        raise HTTPException(status_code=404, detail="Principal not found")

    definition = None
    if payload.search_definition_id is not None:
        definition = db.get(SearchDefinition, payload.search_definition_id)
        if not definition:
            raise HTTPException(status_code=404, detail="Search definition not found")

    people_first = payload.people_first if payload.people_first is not None else True
    criteria = _criteria_from_request(payload, definition)

    # Pre-create the run row so the client has an id to poll while it runs.
    provider = get_discovery_provider()
    discovery_run = DiscoveryRun(
        principal_id=principal.id,
        search_definition_id=payload.search_definition_id,
        provider=getattr(provider, "name", "stub"),
        criteria=_criteria_to_dict(criteria),
        status=DiscoveryStatus.PENDING,
        requested_by=payload.requested_by or "user",
    )
    db.add(discovery_run)
    db.commit()
    db.refresh(discovery_run)

    launch_discovery(
        discovery_run.id,
        principal.id,
        criteria,
        search_definition_id=payload.search_definition_id,
        requested_by=payload.requested_by or "user",
        generate_insights=payload.generate_insights,
        include_organizations=payload.include_organizations,
        people_first=people_first,
        auto_expand_to_target=payload.auto_expand_to_target,
        search_goal=payload.search_goal.strip() if payload.search_goal else None,
        auto_process=payload.auto_process,
        require_email_and_linkedin=payload.require_email_and_linkedin,
    )
    return _discovery_run_out(discovery_run)


@router.get("/runs", response_model=Page[DiscoveryRunOut])
def list_runs(
    db: Session = Depends(get_db),
    principal_id: Optional[int] = None,
    limit: int = Query(25, le=200),
    offset: int = 0,
):
    query = select(DiscoveryRun)
    count_query = select(func.count()).select_from(DiscoveryRun)
    if principal_id is not None:
        query = query.where(DiscoveryRun.principal_id == principal_id)
        count_query = count_query.where(DiscoveryRun.principal_id == principal_id)
    query = query.order_by(DiscoveryRun.created_at.desc()).limit(limit).offset(offset)
    items = db.execute(query).scalars().all()
    total = db.execute(count_query).scalar_one()
    researched = _researched_counts(db, [r.id for r in items])
    return Page[DiscoveryRunOut](
        items=[_discovery_run_out(r, researched=researched.get(r.id, 0)) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}", response_model=DiscoveryRunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    discovery_run = db.get(DiscoveryRun, run_id)
    if not discovery_run:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    researched = _researched_counts(db, [run_id]).get(run_id, 0)
    return _discovery_run_out(discovery_run, researched=researched)


@router.delete("/runs/{run_id}")
def delete_run(run_id: int, db: Session = Depends(get_db)):
    """Delete one discovery run and its prospects (frees global dedup)."""
    try:
        result = delete_discovery_run(db, run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.post("/runs/{run_id}/process", response_model=DiscoveryProcessSummary)
def process_run(run_id: int, db: Session = Depends(get_db)):
    """Research and reveal contact details for every prospect in a run."""
    run = db.get(DiscoveryRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    principal = db.get(Principal, run.principal_id) if run.principal_id else None
    if not principal:
        raise HTTPException(status_code=400, detail="Run has no principal")
    try:
        return process_discovery_run(
            db, run_id, principal=principal, skip_existing_research=False
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- Run-level bulk jobs (all run in the background; poll GET /runs/{id}) ----

def _run_for_job(db: Session, run_id: int) -> DiscoveryRun:
    run = db.get(DiscoveryRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    if run.job_status == JOB_RUNNING:
        raise HTTPException(
            status_code=409,
            detail=f"A '{run.job_kind}' job is already running for this run.",
        )
    return run


@router.post("/runs/{run_id}/reveal", response_model=DiscoveryRunOut, status_code=202)
def reveal_run_emails(run_id: int, db: Session = Depends(get_db)):
    """Reveal email/phone for every unrevealed prospect in the run (background)."""
    run = _run_for_job(db, run_id)
    launch_run_reveal(run_id)
    db.refresh(run)
    return _discovery_run_out(run)


@router.post("/runs/{run_id}/approve", response_model=DiscoveryRunOut, status_code=202)
def approve_run_prospects(
    run_id: int,
    contact_ids: Optional[list[int]] = Body(default=None, embed=True),
    db: Session = Depends(get_db),
):
    """Research + reveal + approve many prospects at once, several in parallel
    (background). ``contact_ids`` limits this to a specific selection (e.g.
    checkboxes on the Prospects page); omit to approve every not-yet-approved
    prospect in the run.
    """
    run = _run_for_job(db, run_id)
    launch_run_approve(run_id, contact_ids)
    db.refresh(run)
    return _discovery_run_out(run)


@router.post("/runs/{run_id}/pipeline", response_model=DiscoveryRunOut, status_code=202)
def pipeline_run_prospects(
    run_id: int,
    contact_ids: Optional[list[int]] = Body(default=None, embed=True),
    outreach_goal: Optional[str] = Body(default=None, embed=True),
    db: Session = Depends(get_db),
):
    """Approve + draft + send many prospects in one background job, with sending
    overlapped against still-in-progress approve/draft work instead of waiting
    for the whole batch to be drafted first (see discovery_jobs.py's pipeline
    section). ``contact_ids`` limits this to a specific selection; omit to
    target every prospect in the run that isn't already approved.
    """
    run = _run_for_job(db, run_id)
    launch_run_pipeline(run_id, contact_ids, outreach_goal=outreach_goal)
    db.refresh(run)
    return _discovery_run_out(run)


@router.post("/runs/{run_id}/draft-emails", response_model=DiscoveryRunOut, status_code=202)
def draft_run_emails(
    run_id: int,
    outreach_goal: Optional[str] = Body(default=None, embed=True),
    principal_id: Optional[int] = Body(default=None, embed=True),
    db: Session = Depends(get_db),
):
    """Draft outreach emails for every approved prospect in the run (background).

    Returns immediately with 202; progress lands on the run's ``job_*`` columns.
    Drafting is one LLM call per prospect, so a few hundred approved prospects
    takes far longer than any HTTP timeout — this must never be done inline.

    ``outreach_goal`` is the purpose the operator typed on the Drafts page; it
    steers what every email argues. Falls back to the run's stored goal.

    ``principal_id`` optionally drafts these already-discovered prospects as a
    DIFFERENT principal than the one whose search actually found them — reuses
    this run's (already paid for) prospect list instead of re-running Apollo
    discovery just to send from another identity. Omit to draft as the run's
    own principal (existing behaviour).
    """
    run = _run_for_job(db, run_id)
    if principal_id is not None:
        if db.get(Principal, principal_id) is None:
            raise HTTPException(status_code=404, detail="Principal not found")
    elif run.principal_id is None:
        raise HTTPException(status_code=400, detail="Run has no principal")
    launch_run_draft(run_id, outreach_goal=outreach_goal, draft_principal_id=principal_id)
    db.refresh(run)
    return _discovery_run_out(run)


@router.post("/runs/{run_id}/draft-linkedin", response_model=DiscoveryRunOut, status_code=202)
def draft_run_linkedin(
    run_id: int,
    outreach_goal: Optional[str] = Body(default=None, embed=True),
    principal_id: Optional[int] = Body(default=None, embed=True),
    db: Session = Depends(get_db),
):
    """Draft LinkedIn messages for every approved prospect in the run (background).

    Returns 202 immediately; progress lands on the run's ``job_*`` columns. Like
    email drafting, this is one LLM call per prospect — done inline it outlived
    both the browser's timeout and the worker's, and the old route's single
    end-of-loop commit meant a killed request saved nothing at all.
    """
    run = _run_for_job(db, run_id)
    if run.principal_id is None and principal_id is None:
        raise HTTPException(status_code=400, detail="Run has no principal")
    launch_run_linkedin_draft(
        run_id, outreach_goal=outreach_goal, draft_principal_id=principal_id
    )
    db.refresh(run)
    return _discovery_run_out(run)


@router.post("/runs/{run_id}/send-emails", response_model=DiscoveryRunOut, status_code=202)
def send_run_emails(run_id: int, db: Session = Depends(get_db)):
    """Approve + send every draft/approved email in the run, paced (background)."""
    run = _run_for_job(db, run_id)
    launch_run_email_send(run_id)
    db.refresh(run)
    return _discovery_run_out(run)


@router.post("/runs/{run_id}/send-linkedin", response_model=DiscoveryRunOut, status_code=202)
def send_run_linkedin(run_id: int, db: Session = Depends(get_db)):
    """Approve + send every draft/approved LinkedIn message in the run (background)."""
    run = _run_for_job(db, run_id)
    launch_run_linkedin_send(run_id)
    db.refresh(run)
    return _discovery_run_out(run)


@router.post("/runs/{run_id}/cancel-job", response_model=DiscoveryRunOut)
def cancel_run_job(run_id: int, db: Session = Depends(get_db)):
    """Ask the running bulk job to stop after the item it is working on."""
    run = db.get(DiscoveryRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    if run.job_status == JOB_RUNNING:
        run.job_cancel_requested = True
        db.commit()
        db.refresh(run)
    return _discovery_run_out(run)
