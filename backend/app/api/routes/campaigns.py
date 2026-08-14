"""Campaign CRUD + dashboards.

A "campaign" is one ``AgentConfig`` row (its own goal/playbook, schedule, runs,
prospects and email history). A principal can own many campaigns; they share the
principal's mailbox daily cap for deliverability.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.agent_config import AgentConfig
from app.models.agent_copy_variant import AgentCopyVariant
from app.models.agent_playbook import AgentPlaybook
from app.models.agent_run import AgentRun
from app.models.agent_variant import AgentVariant
from app.models.bulk_campaign import BulkLookup
from app.models.call import Call
from app.models.contact import Contact
from app.models.email_draft import EmailDraft
from app.models.enums import AuditAction
from app.models.linkedin_message import LinkedInMessage
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.models.suppression import OutreachHistory
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
from app.services.audit import log_action
from app.services import campaign_bulk_send
from app.services.campaign_control import (
    UnscheduleResult,
    request_run_cancel,
    reschedule_campaign_emails,
    unschedule_campaign_emails,
)

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
    if payload.qualify_min is not None:
        config.qualify_min = float(payload.qualify_min)
    if payload.auto_reject_below is not None:
        config.auto_reject_below = float(payload.auto_reject_below)
    if payload.require_email_and_linkedin is not None:
        config.require_email_and_linkedin = bool(payload.require_email_and_linkedin)
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

    # The campaign is committed (and its first run launched) at this point, so
    # the response must not be able to fail the request: a dashboard-aggregation
    # error here made the UI report "could not create" for campaigns that WERE
    # created, and every retry stacked a duplicate. Fall back to the minimal
    # summary instead.
    try:
        summary = list_campaigns(db, days=14)
        for item in summary["items"]:
            if item["id"] == config.id:
                return item
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception(
            "list_campaigns failed while building the create response; "
            "returning minimal summary for campaign %s",
            config.id,
        )
    # Fallback minimal summary.
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
        "require_email_and_linkedin",
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
    skip_discovery: bool = Query(
        False,
        description=(
            "Resume-only: skip finding new people and only finish the existing "
            "backlog from earlier runs. Ignored unless resume=true."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Kick off a run for this campaign now (executes in the background)."""
    config = _get_campaign(db, campaign_id)
    if config.paused:
        raise HTTPException(
            status_code=409,
            detail="This campaign is paused. Resume it before running.",
        )
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
        skip_discovery=skip_discovery and resume,
    )
    return campaign_detail(db, campaign_id)


@router.post("/{campaign_id}/cancel", response_model=CampaignDetailOut)
def cancel_campaign_run(campaign_id: int, db: Session = Depends(get_db)):
    """Stop the in-flight run AND cancel anything it already queued.

    Halting the run alone would still let scheduled emails go out, so queued
    sends are pulled back to APPROVED here too.
    """
    _get_campaign(db, campaign_id)
    was_running = request_run_cancel(db, campaign_id)
    result = unschedule_campaign_emails(db, campaign_id)
    if not was_running and not result.cancelled and not result.failed_ids:
        raise HTTPException(
            status_code=404,
            detail="Nothing to stop — no run in progress and no scheduled emails.",
        )
    log_action(
        db,
        AuditAction.CAMPAIGN,
        entity_type="campaign",
        entity_id=campaign_id,
        summary=f"Stopped campaign run. {result.message()}",
    )
    db.commit()
    return campaign_detail(db, campaign_id)


@router.post("/{campaign_id}/pause", response_model=CampaignDetailOut)
def pause_campaign(
    campaign_id: int,
    keep_scheduled: bool = Query(
        False,
        description=(
            "If true, stop finding people / daily runs but leave already-scheduled "
            "emails to send. If false (default), cancel every queued email too."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Pause a campaign until explicitly resumed.

    Always stops any in-flight run and turns off daily automation. By default
    also cancels queued emails; pass ``keep_scheduled=true`` to let the existing
    send queue finish.

    Only ``paused`` is set here, not ``enabled`` — the scheduler already skips
    any campaign where either is true, so ``paused`` alone fully blocks daily
    automation. Leaving ``enabled`` untouched means resume can restore it
    exactly as it was instead of guessing it should always come back on.
    """
    config = _get_campaign(db, campaign_id)
    config.paused = True
    request_run_cancel(db, campaign_id)
    if keep_scheduled:
        result = UnscheduleResult()
        summary = "Paused campaign. Daily runs off; scheduled emails will still send."
    else:
        result = unschedule_campaign_emails(db, campaign_id)
        summary = f"Paused campaign. {result.message()}"
    log_action(
        db,
        AuditAction.CAMPAIGN,
        entity_type="campaign",
        entity_id=campaign_id,
        summary=summary,
    )
    db.commit()
    if not result.ok:
        # The campaign is paused either way; surface the Outlook copies we
        # could not delete so they can be handled in the mailbox.
        raise HTTPException(status_code=207, detail=result.message())
    return campaign_detail(db, campaign_id)


@router.post("/{campaign_id}/resume", response_model=CampaignDetailOut)
def resume_campaign(
    campaign_id: int,
    reschedule: bool = Query(
        True, description="Re-queue emails the pause cancelled, using AI send timing"
    ),
    db: Session = Depends(get_db),
):
    """Lift a pause and re-queue cancelled mail.

    Only clears ``paused``. ``enabled`` (the "run automatically every day"
    setting) is left exactly as the operator set it — a campaign that was
    created or edited with daily automation off stays off after a pause/resume
    cycle instead of silently being turned back on.

    Emails pulled back by a full pause are scheduled again with the same
    per-recipient timing the agent uses.
    """
    config = _get_campaign(db, campaign_id)
    config.paused = False
    db.flush()
    scheduled = reschedule_campaign_emails(db, campaign_id) if reschedule else 0
    log_action(
        db,
        AuditAction.CAMPAIGN,
        entity_type="campaign",
        entity_id=campaign_id,
        summary=f"Resumed campaign. Re-scheduled {scheduled} email(s).",
    )
    db.commit()
    return campaign_detail(db, campaign_id)


@router.post("/{campaign_id}/schedule-approved", response_model=CampaignDetailOut)
def schedule_approved_emails(campaign_id: int, db: Session = Depends(get_db)):
    """Queue this campaign's approved-but-unscheduled emails with AI send timing.

    Lets a batch left over from a pause (or an earlier run that hit the daily
    cap) go out on sensible per-recipient times instead of being sent by hand.
    """
    config = _get_campaign(db, campaign_id)
    if config.paused:
        raise HTTPException(
            status_code=409,
            detail="This campaign is paused. Resume it before scheduling sends.",
        )
    scheduled = reschedule_campaign_emails(db, campaign_id)
    if not scheduled:
        raise HTTPException(
            status_code=400,
            detail=(
                "Nothing to schedule — no approved emails with a valid address "
                "are waiting."
            ),
        )
    log_action(
        db,
        AuditAction.CAMPAIGN,
        entity_type="campaign",
        entity_id=campaign_id,
        summary=f"Scheduled {scheduled} approved email(s)",
    )
    db.commit()
    return campaign_detail(db, campaign_id)


@router.post("/{campaign_id}/send-drafts", status_code=202)
def send_all_drafts(campaign_id: int, db: Session = Depends(get_db)):
    """Approve + send every draft waiting in this campaign, in the background.

    The panel's "Approve & send all" used to drive this loop from the browser, two
    requests per draft, which stopped partway whenever the tab closed. This returns
    as soon as the job is spawned; poll the GET below for progress.
    """
    config = _get_campaign(db, campaign_id)
    if config.paused:
        raise HTTPException(
            status_code=409,
            detail="This campaign is paused. Resume it before sending.",
        )
    try:
        state = campaign_bulk_send.start(db, campaign_id)
    except campaign_bulk_send.BulkSendError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    log_action(
        db,
        AuditAction.CAMPAIGN,
        entity_type="campaign",
        entity_id=campaign_id,
        summary=f"Started bulk approve+send of {state['total']} draft(s)",
    )
    db.commit()
    return state


@router.get("/{campaign_id}/send-drafts")
def send_all_drafts_progress(campaign_id: int, db: Session = Depends(get_db)):
    """Progress of this campaign's bulk send, or ``null`` if it has never run.

    ``status`` is running | done | cancelled | interrupted. "interrupted" means a
    restart killed the worker; the emails that had not gone out are still drafts,
    so POSTing again continues from where it stopped.
    """
    _get_campaign(db, campaign_id)
    return campaign_bulk_send.state_for(campaign_id)


@router.post("/{campaign_id}/send-drafts/cancel")
def cancel_send_all_drafts(campaign_id: int, db: Session = Depends(get_db)):
    """Stop a running bulk send after the draft it is currently on."""
    _get_campaign(db, campaign_id)
    if not campaign_bulk_send.request_cancel(campaign_id):
        raise HTTPException(
            status_code=409, detail="No bulk send is running for this campaign."
        )
    log_action(
        db,
        AuditAction.CAMPAIGN,
        entity_type="campaign",
        entity_id=campaign_id,
        summary="Requested stop of bulk approve+send",
    )
    db.commit()
    return campaign_bulk_send.state_for(campaign_id)


def _contacts_to_detach(
    db: Session, campaign_id: int, contact_ids: list[int]
) -> set[int]:
    """Which of a campaign's prospects must OUTLIVE the campaign.

    Deleting a campaign was written as "delete its prospects", which is right for
    a prospect the campaign only ever discovered. It is wrong for one that was
    actually reached: the LinkedIn row recording that touch points at the contact,
    so removing the contact removes the touch — and that row is what feeds
    per-account acceptance/reply rates AND the "never invite the same person
    twice" guard (``_already_contacted_on_linkedin``, matched on LinkedIn
    identity). Losing it means the same person can be invited again later, which
    is exactly what gets a sending account flagged.

    So a prospect with real outreach history is kept and merely detached from the
    campaign. Returned here; everything else is still deleted as before.

    Covers every table that references a contact and is NOT removed by this
    endpoint — LinkedIn messages, calls, the outreach cooldown log, bulk-list
    lookups, and drafts belonging to some other campaign. Each of those was
    previously an unhandled foreign key: Postgres refused the delete and rolled
    the whole transaction back, so the campaign silently would not delete.
    """
    if not contact_ids:
        return set()

    keep: set[int] = set()
    for query in (
        select(LinkedInMessage.contact_id).where(
            LinkedInMessage.contact_id.in_(contact_ids)
        ),
        select(Call.contact_id).where(Call.contact_id.in_(contact_ids)),
        select(OutreachHistory.contact_id).where(
            OutreachHistory.contact_id.in_(contact_ids)
        ),
        select(BulkLookup.contact_id).where(BulkLookup.contact_id.in_(contact_ids)),
        # Drafts from another campaign are not deleted here, so their prospect
        # cannot be either.
        select(EmailDraft.contact_id).where(
            EmailDraft.contact_id.in_(contact_ids),
            or_(
                EmailDraft.campaign_id.is_(None),
                EmailDraft.campaign_id != campaign_id,
            ),
        ),
    ):
        keep.update(cid for (cid,) in db.execute(query).all() if cid is not None)

    # An insight cannot be deleted while something that survives still points at
    # it, and a surviving insight in turn pins its prospect — so keep that
    # prospect too rather than discovering the constraint at commit time.
    insight_owner = {
        iid: cid
        for iid, cid in db.execute(
            select(RelevanceInsight.id, RelevanceInsight.contact_id).where(
                RelevanceInsight.contact_id.in_(contact_ids)
            )
        ).all()
    }
    if insight_owner:
        insight_ids = list(insight_owner)
        for query in (
            select(LinkedInMessage.insight_id).where(
                LinkedInMessage.insight_id.in_(insight_ids)
            ),
            select(Call.insight_id).where(Call.insight_id.in_(insight_ids)),
            select(EmailDraft.insight_id).where(
                EmailDraft.insight_id.in_(insight_ids),
                or_(
                    EmailDraft.campaign_id.is_(None),
                    EmailDraft.campaign_id != campaign_id,
                ),
            ),
        ):
            for (iid,) in db.execute(query).all():
                owner = insight_owner.get(iid)
                if owner is not None:
                    keep.add(owner)
    return keep


def _delete_orphan_playbook(db: Session, playbook_id: int) -> None:
    """Drop a playbook no campaign uses any more, with what hangs off it.

    This used to delete the playbook row on its own, but a playbook owns the A/B
    machinery the dashboard shows as "who we target" and "how we write"
    (``AgentVariant`` / ``AgentCopyVariant``), and those reference it. So the
    delete raised ForeignKeyViolation — *after* the campaign had already been
    committed as deleted, which turned a successful delete into a 500 and left
    the UI sitting on a campaign that no longer existed.

    Only reached when no campaign points at the playbook, so its variants belong
    to nothing. The references cleared below are A/B attribution only ("which
    variant wrote this draft"), never outreach content, so history stays intact.
    """
    still_used = db.execute(
        select(func.count())
        .select_from(AgentConfig)
        .where(AgentConfig.playbook_id == playbook_id)
    ).scalar_one()
    if still_used:
        return
    playbook = db.get(AgentPlaybook, playbook_id)
    if playbook is None:
        return

    copy_variant_ids = [
        vid
        for (vid,) in db.execute(
            select(AgentCopyVariant.id).where(
                AgentCopyVariant.playbook_id == playbook_id
            )
        ).all()
    ]
    variant_ids = [
        vid
        for (vid,) in db.execute(
            select(AgentVariant.id).where(AgentVariant.playbook_id == playbook_id)
        ).all()
    ]

    if copy_variant_ids:
        for draft in db.execute(
            select(EmailDraft).where(EmailDraft.copy_variant_id.in_(copy_variant_ids))
        ).scalars().all():
            draft.copy_variant_id = None
    if variant_ids:
        for contact in db.execute(
            select(Contact).where(Contact.variant_id.in_(variant_ids))
        ).scalars().all():
            contact.variant_id = None
        for run in db.execute(
            select(AgentRun).where(AgentRun.variant_id.in_(variant_ids))
        ).scalars().all():
            run.variant_id = None
    for run in db.execute(
        select(AgentRun).where(AgentRun.playbook_id == playbook_id)
    ).scalars().all():
        run.playbook_id = None

    for copy_variant in db.execute(
        select(AgentCopyVariant).where(AgentCopyVariant.playbook_id == playbook_id)
    ).scalars().all():
        db.delete(copy_variant)
    for variant in db.execute(
        select(AgentVariant).where(AgentVariant.playbook_id == playbook_id)
    ).scalars().all():
        db.delete(variant)
    # Flushed before the playbook for the same reason as above: without ORM
    # relationships the session will not order these two deletes itself.
    db.flush()
    db.delete(playbook)
    db.commit()


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
    # Prospects that were actually reached are kept (see _contacts_to_detach);
    # the rest are deleted exactly as before.
    detach_ids = _contacts_to_detach(db, campaign_id, contact_ids)
    removable_ids = [cid for cid in contact_ids if cid not in detach_ids]

    # LinkedIn rows outlive the campaign for the same reason their prospects do,
    # so only the campaign link is cleared. Nothing about the message, its
    # sending account, or its timestamps changes, which is what keeps the
    # dashboard's per-account figures and the duplicate-invite guard correct.
    for message in db.execute(
        select(LinkedInMessage).where(LinkedInMessage.campaign_id == campaign_id)
    ).scalars().all():
        message.campaign_id = None

    if detach_ids:
        for contact in db.execute(
            select(Contact).where(Contact.id.in_(detach_ids))
        ).scalars().all():
            contact.campaign_id = None

    # These models declare foreign keys but no ORM ``relationship()``, so the
    # unit of work has no dependency graph to sort a flush by and will happily
    # emit ``DELETE FROM agent_configs`` before the rows pointing at it. Each
    # stage below is therefore flushed in dependency order by hand — children
    # first, parents last — rather than left to the ORM's ordering. Same rows
    # are removed as before; only the order they reach the database is pinned.
    for run in db.execute(
        select(AgentRun).where(AgentRun.campaign_id == campaign_id)
    ).scalars().all():
        db.delete(run)

    # Cancel queued sends before deleting the drafts. Outlook-deferred copies
    # live at Exchange and would still be delivered after the local row is gone.
    unschedule_campaign_emails(db, campaign_id)
    for draft in db.execute(
        select(EmailDraft).where(EmailDraft.campaign_id == campaign_id)
    ).scalars().all():
        db.delete(draft)
    # Drafts carry both a contact and an insight, so they have to go before
    # either of those can be removed.
    db.flush()

    if removable_ids:
        for insight in db.execute(
            select(RelevanceInsight).where(
                RelevanceInsight.contact_id.in_(removable_ids)
            )
        ).scalars().all():
            db.delete(insight)
        db.flush()
        for contact in db.execute(
            select(Contact).where(Contact.id.in_(removable_ids))
        ).scalars().all():
            db.delete(contact)
        db.flush()

    db.delete(config)
    db.commit()
    # Best-effort cleanup of the campaign's own playbook (if not shared). The
    # campaign is already committed as deleted above, so this must never be able
    # to fail the request: it said "best-effort" but had no guard, and the
    # foreign-key error it raised surfaced as a 500 on a delete that had in fact
    # succeeded. Leaving an unused playbook behind is harmless — nothing lists it
    # once no campaign points at it.
    if playbook_id:
        try:
            _delete_orphan_playbook(db, playbook_id)
        except Exception:  # noqa: BLE001 - cleanup must not fail a done delete
            db.rollback()
            logging.getLogger(__name__).exception(
                "Could not clean up playbook %s after deleting campaign %s; "
                "leaving it in place",
                playbook_id,
                campaign_id,
            )
