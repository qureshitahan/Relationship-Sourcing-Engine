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
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.agent_config import AgentConfig
from app.models.bulk_campaign import BulkCampaign
from app.models.email_draft import EmailDraft
from app.models.enums import EmailStatus, LinkedInStatus
from app.models.linkedin_follower import (
    FollowerSendStatus,
    LinkedInFollower,
    LinkedInFollowerSend,
)
from app.models.linkedin_message import LinkedInMessage
from app.models.principal import Principal
from app.schemas.entities import (
    AnalyticsChannel,
    AnalyticsFilterOption,
    AnalyticsFollowerAccountRow,
    AnalyticsFollowers,
    AnalyticsGroupRow,
    AnalyticsOut,
    AnalyticsTotals,
    AnalyticsTrendPoint,
)
from app.services.linkedin_account_names import resolved_names as resolved_account_names

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])

#: ``days=0`` means "everything we have". Anything else is a trailing window.
ALL_TIME = 0


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


@dataclass(frozen=True)
class Window:
    """The reporting period, as a half-open interval ``[since, until)``.

    Either end may be absent: no ``since`` means "from the beginning", no
    ``until`` means "up to now". A trailing range (``days``) only ever sets
    ``since``, which is why the preset ranges behave exactly as before — the
    upper bound simply is not there. A custom range sets both.

    The predicate helpers live here so every query in this module bounds its
    dates the same way; a site that built its own comparison would silently
    ignore the end of a custom range.
    """

    since: Optional[datetime] = None
    until: Optional[datetime] = None

    def bounds(self, column) -> list:
        """Predicates placing ``column`` inside the window (no null check)."""
        preds = []
        if self.since is not None:
            preds.append(column >= self.since)
        if self.until is not None:
            preds.append(column < self.until)
        return preds

    def during(self, column) -> list:
        """As ``bounds``, plus the column having a value at all."""
        return [column.is_not(None), *self.bounds(column)]

    def contains(self, column):
        """A single expression: the column has a value and sits in the window."""
        return and_(*self.during(column))


def _resolve_window(
    days: int, start: Optional[date], end: Optional[date]
) -> Window:
    """Build the window from either an explicit range or the trailing preset.

    An explicit ``start``/``end`` wins over ``days`` — a caller that names its
    own dates means them. ``end`` is inclusive to the reader, so it becomes an
    exclusive bound at the start of the following day; otherwise "1 Aug to 7 Aug"
    would silently drop everything that happened on the 7th.
    """
    if start is not None or end is not None:
        return Window(
            since=datetime.combine(start, time.min) if start else None,
            until=datetime.combine(end, time.min) + timedelta(days=1) if end else None,
        )
    if days <= ALL_TIME:
        return Window()
    return Window(since=datetime.utcnow() - timedelta(days=days))


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


def _email_totals(db: Session, win: Window, where: list) -> AnalyticsTotals:
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

    created = win.bounds(EmailDraft.created_at)

    def during(column) -> list:
        return win.during(column)

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
    db: Session, win: Window, where: list
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
        for bound in win.bounds(ts_column):
            q = q.where(bound)
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
    win: Window,
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
        win=win,
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
    win: Window,
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
        win=win,
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
    db: Session, win: Window, where: list
) -> AnalyticsTotals:
    def count(*extra) -> int:
        q = select(func.count()).select_from(LinkedInMessage)
        for w in [*where, *extra]:
            q = q.where(w)
        return int(db.execute(q).scalar_one())

    created = win.bounds(LinkedInMessage.created_at)

    def during(column) -> list:
        return win.during(column)

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
    db: Session, win: Window, where: list
) -> list[AnalyticsTrendPoint]:
    buckets: dict[str, AnalyticsTrendPoint] = {}

    def collect(field: str, ts_column) -> None:
        q = select(_day(ts_column), func.count()).where(ts_column.is_not(None))
        q = _apply(q, where)
        for bound in win.bounds(ts_column):
            q = q.where(bound)
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
    db: Session, win: Window, where: list, names: dict[int, str]
) -> list[AnalyticsGroupRow]:
    rows = _grouped_performance(
        db,
        model=LinkedInMessage,
        key_columns=[LinkedInMessage.campaign_id],
        win=win,
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
    db: Session, win: Window, where: list, names: dict[int, str]
) -> list[AnalyticsGroupRow]:
    rows = _grouped_performance(
        db,
        model=LinkedInMessage,
        key_columns=[LinkedInMessage.principal_id],
        win=win,
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
    win: Window,
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
        return win.contains(column)

    created_bounds = win.bounds(model.created_at)
    created = and_(*created_bounds) if created_bounds else None
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


def _followers_by_account(
    db: Session, win: Window
) -> AnalyticsFollowers:
    """The Followers module split by the account that owns the audience.

    Every figure mirrors a definition the Followers page already uses (see
    ``services/linkedin_followers.account_stats``) rather than inventing a second
    opinion — the two screens must agree. What changes here is only the scope:
    that function answers for one account and one message, this one answers for
    every account across every message.

    Roster counts are all-time by design (a follower has no "followed on" date to
    window by); sends and replies respect the window like the rest of the page.
    """
    account_names = resolved_account_names()

    def _grouped(query) -> dict[str, int]:
        return {acct: int(n or 0) for acct, n in db.execute(query).all() if acct}

    # The roster: one row per (account, follower), so a plain count is the
    # audience size for that account.
    followers = _grouped(
        select(LinkedInFollower.account_id, func.count()).group_by(
            LinkedInFollower.account_id
        )
    )

    # Checkpoint truth, counted DISTINCT: the same follower reached under two
    # different messages is one person contacted, not two.
    contacted = _grouped(
        select(
            LinkedInFollowerSend.account_id,
            func.count(func.distinct(LinkedInFollowerSend.follower_provider_id)),
        )
        .where(LinkedInFollowerSend.status == FollowerSendStatus.SENT)
        .group_by(LinkedInFollowerSend.account_id)
    )

    sent_where = [LinkedInFollowerSend.status == FollowerSendStatus.SENT]
    sent_where.extend(win.bounds(LinkedInFollowerSend.sent_at))
    sent = _grouped(
        select(LinkedInFollowerSend.account_id, func.count())
        .where(*sent_where)
        .group_by(LinkedInFollowerSend.account_id)
    )

    # Replies live on the message, not the checkpoint — the same place the
    # Followers page reads them from. ``follower_id IS NOT NULL`` is what marks a
    # message as a follower DM, the exact inverse of the prospect filter above.
    replied_where = [
        LinkedInMessage.follower_id.is_not(None),
        LinkedInMessage.status == LinkedInStatus.REPLIED,
        LinkedInMessage.from_account.is_not(None),
    ]
    replied_where.extend(win.bounds(LinkedInMessage.replied_at))
    replied = _grouped(
        select(LinkedInMessage.from_account, func.count())
        .where(*replied_where)
        .group_by(LinkedInMessage.from_account)
    )

    # Not a failure: no path was open at the time (not connected, not an open
    # profile, no InMail), and connection state changes, so it is retryable.
    not_reachable = _grouped(
        select(LinkedInFollowerSend.account_id, func.count())
        .where(LinkedInFollowerSend.status == FollowerSendStatus.SKIPPED)
        .group_by(LinkedInFollowerSend.account_id)
    )

    # A claim whose outcome is unknown. Never windowed: an interrupted send stays
    # outstanding until someone looks at it, so hiding it once it ages out of the
    # window would quietly drop the very thing that needs attention.
    needs_review = _grouped(
        select(LinkedInFollowerSend.account_id, func.count())
        .where(LinkedInFollowerSend.status == FollowerSendStatus.CLAIMED)
        .group_by(LinkedInFollowerSend.account_id)
    )

    account_ids = (
        set(followers)
        | set(contacted)
        | set(sent)
        | set(replied)
        | set(not_reachable)
        | set(needs_review)
    )

    rows = [
        AnalyticsFollowerAccountRow(
            account_id=acct,
            account_name=account_names.get(acct),
            followers=followers.get(acct, 0),
            contacted=contacted.get(acct, 0),
            # Clamped: a follower who has since unfollowed can still hold a send
            # checkpoint, which would otherwise show as a negative remainder.
            never_contacted=max(0, followers.get(acct, 0) - contacted.get(acct, 0)),
            sent=sent.get(acct, 0),
            replied=replied.get(acct, 0),
            reply_rate=_rate(replied.get(acct, 0), sent.get(acct, 0)),
            not_reachable=not_reachable.get(acct, 0),
            needs_review=needs_review.get(acct, 0),
        )
        for acct in account_ids
    ]
    # Busiest audience first, then by how much was sent, so the account doing the
    # work leads the table.
    rows.sort(key=lambda r: (-r.followers, -r.sent, r.account_id))

    total_sent = sum(r.sent for r in rows)
    total_replied = sum(r.replied for r in rows)
    totals = AnalyticsFollowerAccountRow(
        account_id="",
        followers=sum(r.followers for r in rows),
        contacted=sum(r.contacted for r in rows),
        never_contacted=sum(r.never_contacted for r in rows),
        sent=total_sent,
        replied=total_replied,
        # From the summed counts, not an average of the per-account rates: a
        # quiet account must not weigh as much as a busy one.
        reply_rate=_rate(total_replied, total_sent),
        not_reachable=sum(r.not_reachable for r in rows),
        needs_review=sum(r.needs_review for r in rows),
    )
    return AnalyticsFollowers(by_account=rows, totals=totals)


@router.get("/analytics", response_model=AnalyticsOut)
def analytics(
    db: Session = Depends(get_db),
    days: int = Query(
        30, ge=0, le=3650, description="Trailing window in days; 0 = all time"
    ),
    start: Optional[date] = Query(
        None, description="Custom range start (inclusive). Overrides days."
    ),
    end: Optional[date] = Query(
        None, description="Custom range end (inclusive). Overrides days."
    ),
    principal_id: Optional[int] = Query(None),
    campaign_id: Optional[int] = Query(None),
):
    # ``days`` keeps its exact meaning; naming a start or end simply takes
    # precedence over it, so every existing caller behaves as it always has.
    win = _resolve_window(days, start, end)

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
        totals=_email_totals(db, win, email_where),
        trend=_email_trend(db, win, email_where),
        by_campaign=_email_by_campaign(db, win, email_where, campaign_names, bulk_names),
        by_principal=_email_by_principal(db, win, email_where, principal_names),
    )
    linkedin = AnalyticsChannel(
        channel="linkedin",
        totals=_linkedin_totals(db, win, linkedin_where),
        trend=_linkedin_trend(db, win, linkedin_where),
        by_campaign=_linkedin_by_campaign(db, win, linkedin_where, campaign_names),
        by_principal=_linkedin_by_principal(db, win, linkedin_where, principal_names),
    )

    # The Followers module has no principal or campaign to filter by — a
    # follower belongs to the account they follow — so those filters simply do
    # not apply here, and the section says so rather than silently reporting
    # unfiltered numbers under an active filter.
    #
    # Guarded because this block was added after the two channels above: a
    # deployment whose follower tables are missing (they come from create_all,
    # which init_db lets fail) must still get its email and LinkedIn analytics
    # rather than a 500.
    try:
        followers = _followers_by_account(db, win)
    except Exception:  # noqa: BLE001 - an added section must not fail the page
        db.rollback()
        logger.warning("follower analytics failed; reporting it as empty", exc_info=True)
        followers = AnalyticsFollowers()

    return AnalyticsOut(
        days=days,
        since=win.since.isoformat() if win.since else None,
        # Reported back as the inclusive day the caller asked for, not the
        # exclusive instant used in the query.
        until=(win.until - timedelta(days=1)).date().isoformat() if win.until else None,
        generated_at=datetime.utcnow().isoformat(),
        email=email,
        linkedin=linkedin,
        followers=followers,
        principals=[
            AnalyticsFilterOption(id=pid, label=name)
            for pid, name in sorted(principal_names.items(), key=lambda kv: kv[1].lower())
        ],
        campaigns=[
            AnalyticsFilterOption(id=cid, label=name)
            for cid, name in sorted(campaign_names.items(), key=lambda kv: kv[1].lower())
        ],
    )
