"""Analytics: email and LinkedIn outreach, reported strictly side by side.

The two channels are kept apart end to end. They already live in separate tables
(``email_drafts`` and ``linkedin_messages``); this module keeps that separation in
the *reporting* layer too — two independent query paths, two blocks in the
response, and deliberately **no combined figure anywhere**. A blended "messages
sent" would be unreadable: an email open has no LinkedIn equivalent, a connection
invitation has no email equivalent, and averaging their reply rates compares
different acts. Anyone wanting a total can add two labelled numbers themselves.

Everything is derived from the rows as they stand, so new activity appears on the
next request with nothing to refresh or recompute.

This endpoint only reads. It adds nothing to what the existing pages compute and
changes none of it: ``/api/stats``, ``/api/linkedin/stats`` and the campaign
dashboard keep their own definitions, and where a figure overlaps (sent, replied,
acceptance) it is computed the same way here on purpose.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.agent_config import AgentConfig
from app.models.bulk_campaign import BulkCampaign
from app.models.email_draft import EmailDraft
from app.models.enums import EmailStatus, LinkedInStatus
from app.models.linkedin_message import LinkedInMessage
from app.models.principal import Principal
from app.schemas.entities import (
    AnalyticsChannel,
    AnalyticsFilterOption,
    AnalyticsGroupRow,
    AnalyticsOut,
    AnalyticsTotals,
    AnalyticsTrendPoint,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])

#: ``days=0`` means "everything we have". Anything else is a trailing window.
ALL_TIME = 0


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _window_start(days: int) -> Optional[datetime]:
    """Start of the reporting window, or None for all time."""
    if days <= ALL_TIME:
        return None
    return datetime.utcnow() - timedelta(days=days)


def _day(column):
    """Calendar day of a timestamp column, as a string, for trend buckets."""
    return func.date(column)


# ---------------------------------------------------------------------------
# Email — reads email_drafts only.
# ---------------------------------------------------------------------------


def _email_filters(principal_id: Optional[int], campaign_id: Optional[int]) -> list:
    where = []
    if principal_id is not None:
        where.append(EmailDraft.principal_id == principal_id)
    if campaign_id is not None:
        where.append(EmailDraft.campaign_id == campaign_id)
    return where


def _email_totals(db: Session, since: Optional[datetime], where: list) -> AnalyticsTotals:
    """Headline counts for the window.

    Each metric is dated by the event it describes: a send counts on the day it
    was sent, a reply on the day it arrived. Dating everything by ``created_at``
    instead would make "last 7 days" mean "drafted in the last 7 days", so a
    message drafted a fortnight ago and sent yesterday would vanish from the
    tiles while still appearing on the trend beside them.

    Pipeline states — total produced, and how many sit in each status — are dated
    by creation, because those describe rows rather than events.
    """

    def count(*extra) -> int:
        q = select(func.count()).select_from(EmailDraft)
        for w in [*where, *extra]:
            q = q.where(w)
        return int(db.execute(q).scalar_one())

    created = [EmailDraft.created_at >= since] if since else []

    def during(column) -> list:
        return [column.is_not(None), *([column >= since] if since else [])]

    by_status = {
        status: int(n or 0)
        for status, n in db.execute(
            _apply(
                select(EmailDraft.status, func.count()).group_by(EmailDraft.status),
                [*where, *created],
            )
        ).all()
    }

    sent = count(*during(EmailDraft.sent_at))
    opened = count(*during(EmailDraft.first_opened_at))
    replied = count(*during(EmailDraft.replied_at))
    return AnalyticsTotals(
        total=count(*created),
        drafts=count(*created, EmailDraft.status == EmailStatus.DRAFT),
        approved=count(*created, EmailDraft.status == EmailStatus.APPROVED),
        scheduled=count(*created, EmailDraft.status == EmailStatus.SCHEDULED),
        sent=sent,
        replied=replied,
        reply_rate=_rate(replied, sent),
        opened=opened,
        open_rate=_rate(opened, sent),
        by_status=by_status,
    )


def _apply(query, where: list):
    for w in where:
        query = query.where(w)
    return query


def _email_trend(
    db: Session, since: Optional[datetime], where: list
) -> list[AnalyticsTrendPoint]:
    """Daily counts, each event on the day it happened.

    Three separate groupings rather than one: a draft created on Monday and sent
    on Wednesday belongs to Monday's "created" and Wednesday's "sent". Bucketing
    everything by one timestamp would misdate the other two.
    """
    buckets: dict[str, AnalyticsTrendPoint] = {}

    def collect(field: str, ts_column, extra: Optional[list] = None) -> None:
        q = select(_day(ts_column), func.count()).where(ts_column.is_not(None))
        q = _apply(q, [*where, *(extra or [])])
        if since is not None:
            q = q.where(ts_column >= since)
        for day, n in db.execute(q.group_by(_day(ts_column))).all():
            if not day:
                continue
            point = buckets.setdefault(str(day), AnalyticsTrendPoint(date=str(day)))
            setattr(point, field, int(n or 0))

    collect("created", EmailDraft.created_at)
    collect("sent", EmailDraft.sent_at)
    collect("opened", EmailDraft.first_opened_at)
    collect("replied", EmailDraft.replied_at)
    return [buckets[d] for d in sorted(buckets)]


def _merge(rows: list[tuple[str | None, str, int, int, int]]) -> list[AnalyticsGroupRow]:
    """Fold rows sharing a key into one, then rank by sends.

    Two database groups can resolve to the same reported group — a bulk campaign
    reached through several row shapes, for instance — so totals are summed and
    the rate recomputed from the summed parts rather than averaged.
    """
    merged: dict[str, dict[str, Any]] = {}
    for key, label, total, sent, replied in rows:
        slot = merged.setdefault(
            key or f"~{label}", {"key": key, "label": label, "t": 0, "s": 0, "r": 0}
        )
        slot["t"] += total
        slot["s"] += sent
        slot["r"] += replied
    out = [
        AnalyticsGroupRow(
            key=v["key"],
            label=v["label"],
            total=v["t"],
            sent=v["s"],
            replied=v["r"],
            reply_rate=_rate(v["r"], v["s"]),
        )
        for v in merged.values()
    ]
    out.sort(key=lambda r: (-r.sent, -r.total))
    return out


def _email_by_campaign(
    db: Session,
    since: Optional[datetime],
    where: list,
    names: dict[int, str],
    bulk_names: dict[int, str],
) -> list[AnalyticsGroupRow]:
    """Email performance per campaign, counting BOTH kinds of campaign.

    Two independent features write campaigns here: the agent pipeline stamps
    ``campaign_id``, and the bulk-email module stamps ``bulk_campaign_id`` and
    nothing else. Grouping on ``campaign_id`` alone therefore dropped every bulk
    campaign into a single anonymous "No campaign" row, hiding a whole module's
    output. Both columns are read, and a row is attributed to whichever it
    carries — preferring the agent campaign when a row somehow has both.
    """
    rows = _grouped_performance(
        db,
        model=EmailDraft,
        key_columns=[EmailDraft.campaign_id, EmailDraft.bulk_campaign_id],
        since=since,
        where=where,
        sent_column=EmailDraft.sent_at,
        replied_column=EmailDraft.replied_at,
    )
    labelled = []
    for (campaign_id, bulk_id), total, sent, replied in rows:
        if campaign_id is not None:
            key, label = f"c{campaign_id}", names.get(campaign_id, f"Campaign {campaign_id}")
        elif bulk_id is not None:
            # Marked as bulk so it is never mistaken for an agent campaign of the
            # same name, and so a reader knows which module produced it.
            key = f"b{bulk_id}"
            label = f"{bulk_names.get(bulk_id, f'Bulk campaign {bulk_id}')} (bulk)"
        else:
            key, label = None, "No campaign"
        labelled.append((key, label, total, sent, replied))
    return _merge(labelled)


def _email_by_principal(
    db: Session,
    since: Optional[datetime],
    where: list,
    names: dict[int, str],
) -> list[AnalyticsGroupRow]:
    """Email performance per principal.

    Bulk-email drafts carry no principal at all — the module sends from a mailbox,
    not on someone's behalf — so they would sit in a bare "Unassigned" row that
    says nothing about where they came from. They are named for what they are
    instead. No principal is inferred from the mailbox: that guess would put real
    numbers against the wrong person's name.
    """
    rows = _grouped_performance(
        db,
        model=EmailDraft,
        key_columns=[EmailDraft.principal_id, EmailDraft.bulk_campaign_id],
        since=since,
        where=where,
        sent_column=EmailDraft.sent_at,
        replied_column=EmailDraft.replied_at,
    )
    labelled = []
    for (principal_id, bulk_id), total, sent, replied in rows:
        if principal_id is not None:
            key, label = str(principal_id), names.get(principal_id, "Unassigned")
        elif bulk_id is not None:
            key, label = "bulk", "Bulk emails (no principal)"
        else:
            key, label = None, "Unassigned"
        labelled.append((key, label, total, sent, replied))
    return _merge(labelled)


# ---------------------------------------------------------------------------
# LinkedIn — reads linkedin_messages only.
# ---------------------------------------------------------------------------


def _linkedin_filters(principal_id: Optional[int], campaign_id: Optional[int]) -> list:
    # Prospect outreach only, matching how the LinkedIn module scopes its own
    # lists and counts. Follower DMs belong to the Followers module and are
    # reported there; folding a bulk blast in here would swamp these rates.
    where = [LinkedInMessage.follower_id.is_(None)]
    if principal_id is not None:
        where.append(LinkedInMessage.principal_id == principal_id)
    if campaign_id is not None:
        where.append(LinkedInMessage.campaign_id == campaign_id)
    return where


def _linkedin_totals(
    db: Session, since: Optional[datetime], where: list
) -> AnalyticsTotals:
    def count(*extra) -> int:
        q = select(func.count()).select_from(LinkedInMessage)
        for w in [*where, *extra]:
            q = q.where(w)
        return int(db.execute(q).scalar_one())

    created = [LinkedInMessage.created_at >= since] if since else []

    def during(column) -> list:
        return [column.is_not(None), *([column >= since] if since else [])]

    by_status = {
        status: int(n or 0)
        for status, n in db.execute(
            _apply(
                select(LinkedInMessage.status, func.count()).group_by(
                    LinkedInMessage.status
                ),
                [*where, *created],
            )
        ).all()
    }

    invited = count(*during(LinkedInMessage.invitation_sent_at))
    # Same definition as invite_stats in api/routes/linkedin.py, deliberately:
    # an accepted invite is one whose message was delivered afterwards, plus the
    # case where the profile came back 1st-degree but the auto-DM failed.
    #
    # Measured as a cohort, not as an event: invitations SENT in the window, and
    # whether each was accepted by now. Acceptance carries no timestamp of its
    # own, so there is no "accepted this week" to count — and a cohort rate is
    # what an acceptance rate means anyway.
    accepted = count(
        *during(LinkedInMessage.invitation_sent_at),
        or_(
            LinkedInMessage.sent_at.is_not(None),
            LinkedInMessage.connected.is_(True),
        ),
    )
    sent = count(*during(LinkedInMessage.sent_at))
    replied = count(*during(LinkedInMessage.replied_at))
    # A DM that needed no invitation — the recipient was already connected.
    direct_dms = count(
        *during(LinkedInMessage.sent_at),
        LinkedInMessage.invitation_sent_at.is_(None),
    )
    return AnalyticsTotals(
        total=count(*created),
        drafts=count(*created, LinkedInMessage.status == LinkedInStatus.DRAFT),
        approved=count(*created, LinkedInMessage.status == LinkedInStatus.APPROVED),
        sent=sent,
        replied=replied,
        reply_rate=_rate(replied, sent),
        invited=invited,
        accepted=accepted,
        acceptance_rate=_rate(accepted, invited),
        direct_dms=direct_dms,
        # Invitations plus the DMs that needed none. Disjoint by construction —
        # a row either carries an invitation timestamp or it does not — so nobody
        # is counted twice.
        outreach_total=invited + direct_dms,
        by_status=by_status,
    )


def _linkedin_trend(
    db: Session, since: Optional[datetime], where: list
) -> list[AnalyticsTrendPoint]:
    buckets: dict[str, AnalyticsTrendPoint] = {}

    def collect(field: str, ts_column) -> None:
        q = select(_day(ts_column), func.count()).where(ts_column.is_not(None))
        q = _apply(q, where)
        if since is not None:
            q = q.where(ts_column >= since)
        for day, n in db.execute(q.group_by(_day(ts_column))).all():
            if not day:
                continue
            point = buckets.setdefault(str(day), AnalyticsTrendPoint(date=str(day)))
            setattr(point, field, int(n or 0))

    collect("created", LinkedInMessage.created_at)
    collect("invited", LinkedInMessage.invitation_sent_at)
    collect("sent", LinkedInMessage.sent_at)
    collect("replied", LinkedInMessage.replied_at)
    return [buckets[d] for d in sorted(buckets)]


def _linkedin_by_campaign(
    db: Session, since: Optional[datetime], where: list, names: dict[int, str]
) -> list[AnalyticsGroupRow]:
    rows = _grouped_performance(
        db,
        model=LinkedInMessage,
        key_columns=[LinkedInMessage.campaign_id],
        since=since,
        where=where,
        sent_column=LinkedInMessage.sent_at,
        replied_column=LinkedInMessage.replied_at,
    )
    return [
        AnalyticsGroupRow(
            key=str(key) if key is not None else None,
            label=names.get(key, "No campaign") if key is not None else "No campaign",
            total=total,
            sent=sent,
            replied=replied,
            reply_rate=_rate(replied, sent),
        )
        for (key,), total, sent, replied in rows
    ]


def _linkedin_by_principal(
    db: Session, since: Optional[datetime], where: list, names: dict[int, str]
) -> list[AnalyticsGroupRow]:
    rows = _grouped_performance(
        db,
        model=LinkedInMessage,
        key_columns=[LinkedInMessage.principal_id],
        since=since,
        where=where,
        sent_column=LinkedInMessage.sent_at,
        replied_column=LinkedInMessage.replied_at,
    )
    return [
        AnalyticsGroupRow(
            key=str(key) if key is not None else None,
            label=names.get(key, "Unassigned") if key is not None else "Unassigned",
            total=total,
            sent=sent,
            replied=replied,
            reply_rate=_rate(replied, sent),
        )
        for (key,), total, sent, replied in rows
    ]


# ---------------------------------------------------------------------------
# Shared shape — used by both channels, never across them.
# ---------------------------------------------------------------------------


def _grouped_performance(
    db: Session,
    *,
    model,
    key_columns: list,
    since: Optional[datetime],
    where: list,
    sent_column,
    replied_column,
) -> list[tuple[tuple, int, int, int]]:
    """``(key, total, sent, replied)`` per group, in one pass.

    Conditional aggregates rather than a query per metric: three round trips per
    group would turn a campaign list into dozens of queries.

    Each metric carries its own window, exactly as the totals do — so the query
    itself is NOT filtered by creation date. Filtering it would drop a row that
    was sent inside the window but drafted before it, and that row's send belongs
    in this table.
    """

    def in_window(column):
        present = column.is_not(None)
        return and_(present, column >= since) if since is not None else present

    created = model.created_at >= since if since is not None else None
    total_expr = (
        func.sum(case((created, 1), else_=0)) if created is not None else func.count()
    )

    n = len(key_columns)
    query = select(
        *key_columns,
        total_expr,
        func.sum(case((in_window(sent_column), 1), else_=0)),
        func.sum(case((in_window(replied_column), 1), else_=0)),
    )
    query = _apply(query, where)
    rows = db.execute(query.group_by(*key_columns)).all()
    out = [
        (tuple(row[:n]), int(row[n] or 0), int(row[n + 1] or 0), int(row[n + 2] or 0))
        for row in rows
    ]
    # A group with nothing at all in the window is noise, not information.
    out = [r for r in out if r[1] or r[2] or r[3]]
    # Busiest first, so the campaign carrying the outreach leads the table.
    out.sort(key=lambda r: (-r[2], -r[1]))
    return out


def _campaign_labels(db: Session, principal_names: dict[int, str]) -> dict[int, str]:
    """Campaign labels that are actually distinguishable from one another.

    Campaigns are created with a default name, so several unrelated ones end up
    called "Campaign". Listed plainly, a performance table shows four identical
    rows and the reader cannot tell whose is whose — which defeats the point of
    a per-campaign breakdown. A name shared by more than one campaign therefore
    carries its principal, and if that is still ambiguous, its id.

    Unique names are left exactly as typed; nothing is decorated that does not
    need it.
    """
    configs = list(db.execute(select(AgentConfig).order_by(AgentConfig.id)).scalars().all())

    counts: dict[str, int] = {}
    for config in configs:
        base = (config.name or "").strip() or f"Campaign {config.id}"
        counts[base] = counts.get(base, 0) + 1

    seen: dict[str, int] = {}
    labels: dict[int, str] = {}
    for config in configs:
        base = (config.name or "").strip() or f"Campaign {config.id}"
        if counts[base] == 1:
            labels[config.id] = base
            continue
        owner = principal_names.get(config.principal_id)
        label = f"{base} · {owner}" if owner else base
        # Two campaigns with the same name AND the same principal still have to
        # be told apart, so the id breaks the remaining tie.
        seen[label] = seen.get(label, 0) + 1
        labels[config.id] = label if seen[label] == 1 else f"{label} #{config.id}"
    return labels


@router.get("/analytics", response_model=AnalyticsOut)
def analytics(
    db: Session = Depends(get_db),
    days: int = Query(
        30, ge=0, le=3650, description="Trailing window in days; 0 = all time"
    ),
    principal_id: Optional[int] = Query(None),
    campaign_id: Optional[int] = Query(None),
):
    since = _window_start(days)

    principal_names = {
        p.id: p.name
        for p in db.execute(select(Principal).order_by(Principal.id)).scalars().all()
    }
    campaign_names = _campaign_labels(db, principal_names)
    # The bulk-email module's own campaigns, so its output is named rather than
    # pooled into "No campaign".
    bulk_names = {
        b.id: (b.name or "").strip() or f"Bulk campaign {b.id}"
        for b in db.execute(select(BulkCampaign).order_by(BulkCampaign.id)).scalars().all()
    }

    email_where = _email_filters(principal_id, campaign_id)
    linkedin_where = _linkedin_filters(principal_id, campaign_id)

    email = AnalyticsChannel(
        channel="email",
        totals=_email_totals(db, since, email_where),
        trend=_email_trend(db, since, email_where),
        by_campaign=_email_by_campaign(db, since, email_where, campaign_names, bulk_names),
        by_principal=_email_by_principal(db, since, email_where, principal_names),
    )
    linkedin = AnalyticsChannel(
        channel="linkedin",
        totals=_linkedin_totals(db, since, linkedin_where),
        trend=_linkedin_trend(db, since, linkedin_where),
        by_campaign=_linkedin_by_campaign(db, since, linkedin_where, campaign_names),
        by_principal=_linkedin_by_principal(db, since, linkedin_where, principal_names),
    )

    return AnalyticsOut(
        days=days,
        since=since.isoformat() if since else None,
        generated_at=datetime.utcnow().isoformat(),
        email=email,
        linkedin=linkedin,
        principals=[
            AnalyticsFilterOption(id=pid, label=name)
            for pid, name in sorted(principal_names.items(), key=lambda kv: kv[1].lower())
        ],
        campaigns=[
            AnalyticsFilterOption(id=cid, label=name)
            for cid, name in sorted(campaign_names.items(), key=lambda kv: kv[1].lower())
        ],
    )
