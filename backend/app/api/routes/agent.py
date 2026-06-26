"""Autonomous outreach agent: planning, playbooks, config, runs."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.agent_playbook import AgentPlaybook
from app.models.agent_run import AgentRun
from app.models.principal import Principal
from app.schemas.entities import (
    AgentConfigOut,
    AgentPlanOut,
    AgentPlaybookOut,
    AgentRunOut,
    CampaignListOut,
    Page,
)
from app.schemas.requests import (
    AgentConfigRequest,
    AgentPlanRequest,
    AgentPlaybookRequest,
    AgentRunRequest,
)
from app.services.agent import get_or_create_config, launch_run
from app.services.agent.planner import criteria_from_dict, plan_agent_search
from app.services.agent.dashboard import campaign_dashboard, list_campaigns

router = APIRouter(prefix="/agent", tags=["agent"])


def _sync_run_hour_utc(config) -> None:
    """Map local campaign hour + timezone to UTC for the scheduler."""
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime

        tz_name = (config.timezone or "America/New_York").strip()
        local_h = max(0, min(23, int(config.run_hour_local or 9)))
        local = datetime.now(ZoneInfo(tz_name)).replace(
            hour=local_h, minute=0, second=0, microsecond=0
        )
        config.run_hour_utc = local.astimezone(ZoneInfo("UTC")).hour
    except Exception:  # noqa: BLE001
        pass


def _resolve_principal_id(db: Session, principal_id: Optional[int]) -> int:
    if principal_id is not None:
        if not db.get(Principal, principal_id):
            raise HTTPException(status_code=404, detail="Principal not found")
        return principal_id
    first = db.execute(
        select(Principal).order_by(Principal.id.asc())
    ).scalars().first()
    if not first:
        raise HTTPException(
            status_code=400,
            detail="No principal exists yet. Create a principal first.",
        )
    return first.id


@router.post("/plan", response_model=AgentPlanOut)
def plan_search(payload: AgentPlanRequest, db: Session = Depends(get_db)):
    """Turn a plain-language goal into clarifying questions + search criteria."""
    pid = _resolve_principal_id(db, payload.principal_id)
    principal = db.get(Principal, pid)
    try:
        result = plan_agent_search(
            objective_prompt=payload.objective_prompt,
            principal=principal,
            clarifying_answers=payload.clarifying_answers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    criteria = criteria_from_dict(result.get("criteria") or {})
    return AgentPlanOut(
        questions=result.get("questions") or [],
        criteria=criteria,
        rationale=result.get("rationale"),
    )


@router.get("/playbooks", response_model=Page[AgentPlaybookOut])
def list_playbooks(
    principal_id: Optional[int] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    pid = _resolve_principal_id(db, principal_id)
    query = select(AgentPlaybook).where(AgentPlaybook.principal_id == pid)
    count_q = (
        select(func.count())
        .select_from(AgentPlaybook)
        .where(AgentPlaybook.principal_id == pid)
    )
    query = query.order_by(AgentPlaybook.updated_at.desc()).limit(limit).offset(offset)
    items = db.execute(query).scalars().all()
    total = db.execute(count_q).scalar_one()
    return Page[AgentPlaybookOut](items=items, total=total, limit=limit, offset=offset)


@router.post("/playbooks", response_model=AgentPlaybookOut, status_code=201)
def save_playbook(payload: AgentPlaybookRequest, db: Session = Depends(get_db)):
    """Save a named playbook (prompt + criteria) for re-use."""
    pid = _resolve_principal_id(db, payload.principal_id)
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Playbook name is required.")
    criteria = criteria_from_dict(payload.criteria)
    pb = AgentPlaybook(
        principal_id=pid,
        name=payload.name.strip(),
        objective_prompt=payload.objective_prompt.strip(),
        clarifying_answers=payload.clarifying_answers or {},
        criteria=criteria,
    )
    db.add(pb)
    db.commit()
    db.refresh(pb)
    if payload.set_active:
        config = get_or_create_config(db, pid)
        config.playbook_id = pb.id
        db.commit()
    return pb


@router.delete("/playbooks/{playbook_id}", status_code=204)
def delete_playbook(playbook_id: int, db: Session = Depends(get_db)):
    pb = db.get(AgentPlaybook, playbook_id)
    if not pb:
        raise HTTPException(status_code=404, detail="Playbook not found")
    db.delete(pb)
    db.commit()


@router.get("/config", response_model=AgentConfigOut)
def get_config(principal_id: Optional[int] = None, db: Session = Depends(get_db)):
    pid = _resolve_principal_id(db, principal_id)
    config = get_or_create_config(db, pid)
    _sync_run_hour_utc(config)
    db.commit()
    db.refresh(config)
    return config


@router.put("/config", response_model=AgentConfigOut)
def update_config(
    payload: AgentConfigRequest,
    principal_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    pid = _resolve_principal_id(db, principal_id)
    config = get_or_create_config(db, pid)
    data = payload.model_dump(exclude_unset=True)
    if "run_hour_utc" in data and data["run_hour_utc"] is not None:
        data["run_hour_utc"] = max(0, min(23, int(data["run_hour_utc"])))
    if "digest_recipients" in data and data["digest_recipients"] is not None:
        data["digest_recipients"] = [
            r.strip() for r in data["digest_recipients"] if r and r.strip()
        ]
    for key, value in data.items():
        setattr(config, key, value)
    if any(k in data for k in ("timezone", "run_hour_local")):
        _sync_run_hour_utc(config)
    db.commit()
    db.refresh(config)
    return config


@router.post("/run", response_model=AgentRunOut, status_code=202)
def run_now(payload: AgentRunRequest, db: Session = Depends(get_db)):
    """Kick off an agent run immediately (executes in the background)."""
    pid = _resolve_principal_id(db, payload.principal_id)
    in_flight = db.execute(
        select(func.count())
        .select_from(AgentRun)
        .where(AgentRun.principal_id == pid, AgentRun.status == "running")
    ).scalar_one()
    if in_flight:
        raise HTTPException(
            status_code=409,
            detail="An agent run is already in progress for this principal.",
        )
    playbook_id = payload.playbook_id
    if playbook_id:
        pb = db.get(AgentPlaybook, playbook_id)
        if not pb or pb.principal_id != pid:
            raise HTTPException(status_code=404, detail="Playbook not found")
    else:
        config = get_or_create_config(db, pid)
        playbook_id = config.playbook_id
    if not playbook_id:
        raise HTTPException(
            status_code=400,
            detail="Save a playbook first (describe your goal and click Save playbook).",
        )
    return launch_run(pid, trigger="manual", playbook_id=playbook_id)


def _active_playbook(db: Session, pid: int):
    config = get_or_create_config(db, pid)
    pb = None
    if config.playbook_id:
        pb = db.get(AgentPlaybook, config.playbook_id)
    if pb is None:
        pb = db.execute(
            select(AgentPlaybook)
            .where(AgentPlaybook.principal_id == pid)
            .order_by(AgentPlaybook.updated_at.desc())
        ).scalars().first()
    return pb


@router.get("/variants")
def list_variants(principal_id: Optional[int] = None, db: Session = Depends(get_db)):
    """A/B search variants for the active playbook, with live conversion stats."""
    from app.services.agent.experiments import (
        ensure_variants,
        variant_stats,
    )

    pid = _resolve_principal_id(db, principal_id)
    principal = db.get(Principal, pid)
    pb = _active_playbook(db, pid)
    if pb is None:
        return {"playbook_id": None, "variants": []}
    variants = ensure_variants(db, principal, pb)
    return {
        "playbook_id": pb.id,
        "playbook_name": pb.name,
        "variants": [variant_stats(db, v) for v in variants],
    }


@router.post("/variants/regenerate")
def regenerate_variants_endpoint(
    principal_id: Optional[int] = None, db: Session = Depends(get_db)
):
    """Replace the current A/B variants with a fresh set from the playbook."""
    from app.services.agent.experiments import regenerate_variants, variant_stats

    pid = _resolve_principal_id(db, principal_id)
    principal = db.get(Principal, pid)
    pb = _active_playbook(db, pid)
    if pb is None:
        raise HTTPException(status_code=400, detail="Save a playbook first.")
    variants = regenerate_variants(db, principal, pb)
    return {
        "playbook_id": pb.id,
        "variants": [variant_stats(db, v) for v in variants],
    }


@router.get("/copy-variants")
def list_copy_variants(principal_id: Optional[int] = None, db: Session = Depends(get_db)):
    """A/B email-copy variants for the active playbook, with live reply stats."""
    from app.services.agent.experiments import copy_variant_stats, ensure_copy_variants

    pid = _resolve_principal_id(db, principal_id)
    principal = db.get(Principal, pid)
    pb = _active_playbook(db, pid)
    if pb is None:
        return {"playbook_id": None, "copy_variants": []}
    variants = ensure_copy_variants(db, principal, pb)
    return {
        "playbook_id": pb.id,
        "playbook_name": pb.name,
        "copy_variants": [copy_variant_stats(db, v) for v in variants],
    }


@router.post("/copy-variants/regenerate")
def regenerate_copy_variants_endpoint(
    principal_id: Optional[int] = None, db: Session = Depends(get_db)
):
    """Replace the current A/B email-copy variants with a fresh set."""
    from app.services.agent.experiments import (
        copy_variant_stats,
        regenerate_copy_variants,
    )

    pid = _resolve_principal_id(db, principal_id)
    principal = db.get(Principal, pid)
    pb = _active_playbook(db, pid)
    if pb is None:
        raise HTTPException(status_code=400, detail="Save a playbook first.")
    variants = regenerate_copy_variants(db, principal, pb)
    return {
        "playbook_id": pb.id,
        "copy_variants": [copy_variant_stats(db, v) for v in variants],
    }


@router.get("/dashboard")
def get_campaign_dashboard(
    principal_id: Optional[int] = None,
    days: int = Query(14, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Daily campaign rollup: discovers, sends, replies per calendar day."""
    pid = _resolve_principal_id(db, principal_id)
    return campaign_dashboard(db, pid, days=days)


@router.get("/campaigns", response_model=CampaignListOut)
def get_campaigns(
    days: int = Query(14, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """All principal campaigns with live status — for the multi-campaign UI."""
    return list_campaigns(db, days=days)


@router.get("/runs", response_model=Page[AgentRunOut])
def list_runs(
    principal_id: Optional[int] = None,
    limit: int = Query(25, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    query = select(AgentRun)
    count_query = select(func.count()).select_from(AgentRun)
    if principal_id is not None:
        query = query.where(AgentRun.principal_id == principal_id)
        count_query = count_query.where(AgentRun.principal_id == principal_id)
    query = query.order_by(AgentRun.created_at.desc()).limit(limit).offset(offset)
    items = db.execute(query).scalars().all()
    total = db.execute(count_query).scalar_one()
    return Page[AgentRunOut](items=items, total=total, limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=AgentRunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run
