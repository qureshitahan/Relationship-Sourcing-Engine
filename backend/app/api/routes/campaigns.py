"""Campaign CRUD + dashboards.

A "campaign" is one ``AgentConfig`` row (its own goal/playbook, schedule, runs,
prospects and email history). A principal can own many campaigns; they share the
principal's mailbox daily cap for deliverability.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.agent_config import AgentConfig
from app.models.agent_playbook import AgentPlaybook
from app.models.agent_run import AgentRun
from app.models.contact import Contact
from app.models.email_draft import EmailDraft
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.schemas.entities import (
    CampaignDetailOut,
    CampaignListOut,
    CampaignProspectsOut,
    CampaignSummaryOut,
)
from app.schemas.requests import CampaignCreateRequest, CampaignUpdateRequest
from app.services.agent import launch_run
from app.services.agent.dashboard import (
    campaign_detail,
    campaign_prospects,
    list_campaigns,
)
from app.services.agent.planner import criteria_from_dict

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _sync_run_hour_utc(config: AgentConfig) -> None:
    """Map the campaign's local run hour + timezone to UTC for the scheduler."""
    try:
        from zoneinfo import ZoneInfo

        tz_name = (config.timezone or "America/New_York").strip()
        local_h = max(0, min(23, int(config.run_hour_local or 9)))
        local = datetime.now(ZoneInfo(tz_name)).replace(
            hour=local_h, minute=0, second=0, microsecond=0
        )
        config.run_hour_utc = local.astimezone(ZoneInfo("UTC")).hour
    except Exception:  # noqa: BLE001
        pass


def _get_campaign(db: Session, campaign_id: int) -> AgentConfig:
    config = db.get(AgentConfig, campaign_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return config


def _sync_playbook_people_limit(playbook: AgentPlaybook | None, discover_target: int) -> None:
    """Keep playbook search criteria aligned with the campaign's people/run setting."""
    if playbook is None:
        return
    criteria = dict(playbook.criteria or {})
    criteria["people_limit"] = max(1, int(discover_target))
    playbook.criteria = criteria


@router.get("", response_model=CampaignListOut)
def get_campaigns(
    days: int = Query(14, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Every campaign across principals, with live status + window stats."""
    return list_campaigns(db, days=days)


@router.post("", response_model=CampaignSummaryOut, status_code=201)
def create_campaign(payload: CampaignCreateRequest, db: Session = Depends(get_db)):
    """Create a campaign from the wizard: builds playbook + config, optionally runs."""
    principal = db.get(Principal, payload.principal_id)
    if principal is None:
        raise HTTPException(status_code=404, detail="Principal not found")

    criteria = criteria_from_dict(payload.criteria or {})
    playbook = AgentPlaybook(
        principal_id=principal.id,
        name=payload.name.strip(),
        objective_prompt=payload.objective_prompt.strip(),
        clarifying_answers=payload.clarifying_answers or {},
        criteria=criteria,
    )
    db.add(playbook)
    db.commit()
    db.refresh(playbook)

    config = AgentConfig(
        principal_id=principal.id,
        name=payload.name.strip(),
        playbook_id=playbook.id,
        enabled=bool(payload.enabled),
        digest_recipients=[
            r.strip() for r in (payload.digest_recipients or []) if r and r.strip()
        ],
    )
    if payload.discover_target is not None:
        config.discover_target = max(1, int(payload.discover_target))
    else:
        config.discover_target = 50
    _sync_playbook_people_limit(playbook, config.discover_target)
    if payload.auto_send is not None:
        config.auto_send = bool(payload.auto_send)
    if payload.auto_schedule is not None:
        config.auto_schedule = bool(payload.auto_schedule)
    if payload.followup_enabled is not None:
        config.followup_enabled = bool(payload.followup_enabled)
    if payload.followup_schedule_days is not None:
        config.followup_schedule_days = payload.followup_schedule_days
    if payload.timezone:
        config.timezone = payload.timezone
    if payload.run_hour_local is not None:
        config.run_hour_local = max(0, min(23, int(payload.run_hour_local)))
    if payload.send_window_start_local is not None:
        config.send_window_start_local = max(0, min(23, int(payload.send_window_start_local)))
    if payload.send_window_end_local is not None:
        config.send_window_end_local = max(0, min(23, int(payload.send_window_end_local)))
    _sync_run_hour_utc(config)

    if payload.mailbox_daily_cap is not None:
        principal.mailbox_daily_cap = max(0, int(payload.mailbox_daily_cap))

    db.add(config)
    db.commit()
    db.refresh(config)

    if payload.run_now:
        launch_run(
            principal.id,
            trigger="manual",
            playbook_id=playbook.id,
            campaign_id=config.id,
        )

    summary = list_campaigns(db, days=14)
    for item in summary["items"]:
        if item["id"] == config.id:
            return item
    # Fallback minimal summary (should not happen).
    return {
        "id": config.id,
        "name": config.name or "Campaign",
        "principal_id": principal.id,
        "principal_name": principal.name,
        "playbook_id": playbook.id,
        "playbook_name": playbook.name,
        "objective_preview": (playbook.objective_prompt or "")[:160],
        "enabled": bool(config.enabled),
        "status": "running" if payload.run_now else "ready",
        "current_run_id": None,
        "current_run_discovered": None,
        "current_run_sent": None,
        "last_run_at": None,
        "totals_sent_14d": 0,
        "totals_replies_14d": 0,
    }


@router.get("/{campaign_id}", response_model=CampaignDetailOut)
def get_campaign(
    campaign_id: int,
    days: int = Query(14, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Full dashboard payload for one campaign."""
    try:
        return campaign_detail(db, campaign_id, days=days)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{campaign_id}/prospects", response_model=CampaignProspectsOut)
def get_campaign_prospects(campaign_id: int, db: Session = Depends(get_db)):
    """Prospects this campaign surfaced, with their latest email/reply state."""
    try:
        return campaign_prospects(db, campaign_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{campaign_id}", response_model=CampaignDetailOut)
def update_campaign(
    campaign_id: int,
    payload: CampaignUpdateRequest,
    db: Session = Depends(get_db),
):
    config = _get_campaign(db, campaign_id)
    data = payload.model_dump(exclude_unset=True)

    # Playbook (goal/criteria) updates.
    playbook = db.get(AgentPlaybook, config.playbook_id) if config.playbook_id else None
    if playbook is not None:
        if "objective_prompt" in data and data["objective_prompt"] is not None:
            playbook.objective_prompt = data["objective_prompt"].strip()
        if "clarifying_answers" in data and data["clarifying_answers"] is not None:
            playbook.clarifying_answers = data["clarifying_answers"]
        if "criteria" in data and data["criteria"] is not None:
            playbook.criteria = criteria_from_dict(data["criteria"])
        if "name" in data and data["name"]:
            playbook.name = data["name"].strip()

    # Principal-level shared mailbox cap.
    if "mailbox_daily_cap" in data and data["mailbox_daily_cap"] is not None:
        principal = db.get(Principal, config.principal_id)
        if principal is not None:
            principal.mailbox_daily_cap = max(0, int(data["mailbox_daily_cap"]))

    config_fields = {
        "name",
        "enabled",
        "discover_target",
        "qualify_min",
        "auto_reject_below",
        "auto_send",
        "auto_schedule",
        "followup_enabled",
        "followup_schedule_days",
        "timezone",
        "run_hour_local",
        "send_window_start_local",
        "send_window_end_local",
        "digest_recipients",
    }
    for key in config_fields:
        if key in data and data[key] is not None:
            value = data[key]
            if key == "digest_recipients":
                value = [r.strip() for r in value if r and r.strip()]
            if key == "name":
                value = value.strip()
            if key == "discover_target":
                value = max(1, int(value))
            setattr(config, key, value)

    if "discover_target" in data and data["discover_target"] is not None:
        _sync_playbook_people_limit(playbook, config.discover_target)

    if any(k in data for k in ("timezone", "run_hour_local")):
        _sync_run_hour_utc(config)

    db.commit()
    return campaign_detail(db, campaign_id)


@router.post("/{campaign_id}/run", response_model=CampaignDetailOut, status_code=202)
def run_campaign(
    campaign_id: int,
    resume: bool = Query(False, description="Continue prior run: re-process undrafted people"),
    db: Session = Depends(get_db),
):
    """Kick off a run for this campaign now (executes in the background)."""
    config = _get_campaign(db, campaign_id)
    if not config.playbook_id:
        raise HTTPException(
            status_code=400,
            detail="This campaign has no goal yet. Edit it and describe the goal first.",
        )
    in_flight = db.execute(
        select(func.count())
        .select_from(AgentRun)
        .where(AgentRun.campaign_id == campaign_id, AgentRun.status == "running")
    ).scalar_one()
    if in_flight:
        raise HTTPException(
            status_code=409,
            detail="A run is already in progress for this campaign.",
        )
    launch_run(
        config.principal_id,
        trigger="manual",
        playbook_id=config.playbook_id,
        campaign_id=config.id,
        resume=resume,
    )
    return campaign_detail(db, campaign_id)


@router.post("/{campaign_id}/cancel", response_model=CampaignDetailOut)
def cancel_campaign_run(campaign_id: int, db: Session = Depends(get_db)):
    """Request the in-flight agent run to stop after the current step."""
    _get_campaign(db, campaign_id)
    running = db.execute(
        select(AgentRun).where(
            AgentRun.campaign_id == campaign_id,
            AgentRun.status == "running",
        )
    ).scalars().first()
    if running is None:
        raise HTTPException(status_code=404, detail="No run in progress for this campaign.")
    running.summary = {**(running.summary or {}), "cancel_requested": True}
    db.commit()
    return campaign_detail(db, campaign_id)


@router.delete("/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: int, db: Session = Depends(get_db)):
    config = _get_campaign(db, campaign_id)
    playbook_id = config.playbook_id

    # Remove this campaign's runs, drafts, prospects, and their insights.
    contact_ids = [
        cid
        for (cid,) in db.execute(
            select(Contact.id).where(Contact.campaign_id == campaign_id)
        ).all()
        if cid is not None
    ]
    if contact_ids:
        for insight in db.execute(
            select(RelevanceInsight).where(RelevanceInsight.contact_id.in_(contact_ids))
        ).scalars().all():
            db.delete(insight)
        for contact in db.execute(
            select(Contact).where(Contact.id.in_(contact_ids))
        ).scalars().all():
            db.delete(contact)

    for run in db.execute(
        select(AgentRun).where(AgentRun.campaign_id == campaign_id)
    ).scalars().all():
        db.delete(run)
    for draft in db.execute(
        select(EmailDraft).where(EmailDraft.campaign_id == campaign_id)
    ).scalars().all():
        db.delete(draft)

    db.delete(config)
    db.commit()
    # Best-effort cleanup of the campaign's own playbook (if not shared).
    if playbook_id:
        still_used = db.execute(
            select(func.count())
            .select_from(AgentConfig)
            .where(AgentConfig.playbook_id == playbook_id)
        ).scalar_one()
        if not still_used:
            pb = db.get(AgentPlaybook, playbook_id)
            if pb is not None:
                db.delete(pb)
                db.commit()
