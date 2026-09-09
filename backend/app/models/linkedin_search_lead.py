"""People found through a LinkedIn Classic / Sales Navigator search.

The audience for the "Classic Search LinkedIn" tab, which sources prospects from
LinkedIn's own search instead of Apollo. Two tables, mirroring the followers
module for the same reasons:

``LinkedInSearchLead``
    The roster — one row per (connected account, person, search). A cache of who
    a saved set of filters returned, never outreach state.

``LinkedInSearchSend``
    The checkpoint — one row per (account, lead, campaign). It is what guarantees
    one person is never contacted twice for the same message, and the guarantee
    lives in a UNIQUE index rather than in a worker's memory: a row is inserted
    as a *claim* BEFORE the send is attempted, so a retried or concurrent request
    collides on the index instead of sending a second invitation.

These are intentionally NOT ``Contact`` rows. A search result arrives with a
name, headline and public profile URL and nothing else — no email, no company
record, no discovery run — and writing them into ``contacts`` would change what
the Discover, Prospects, Emails and Campaigns modules list and count. This tab
owns its audience end to end.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class SearchSendStatus:
    """Lifecycle of one lead's checkpoint row.

    Deliberately identical in spirit to ``FollowerSendStatus``: ``CLAIMED`` means
    the claim was written, the send was started, and the outcome is unknown. It
    is never retried automatically — a possible duplicate is worse than a missed
    message — and surfaces to the user as "needs review" instead.
    """

    CLAIMED = "claimed"
    #: A connection invitation went out; the message follows once accepted.
    INVITED = "invited"
    #: A direct message was delivered (they were already a 1st-degree connection).
    SENT = "sent"
    FAILED = "failed"
    #: No path available right now (profile locked, invite limit, not resolvable
    #: by this account). Retryable later, since these change over time.
    SKIPPED = "skipped"

    #: Statuses a later run is allowed to attempt again.
    RETRYABLE = (FAILED, SKIPPED)

    #: Statuses that mean "this person has been reached under this message".
    SETTLED = (CLAIMED, INVITED, SENT)


class LinkedInSearchLead(Base, TimestampMixin):
    """One person returned by one search, for one connected account."""

    __tablename__ = "linkedin_search_leads"
    __table_args__ = (
        # The same person can legitimately appear under two different searches;
        # they are one row per search so each search's result count stays honest.
        UniqueConstraint(
            "account_id",
            "search_key",
            "provider_id",
            name="uq_search_lead_account_search_person",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: Which connected LinkedIn account ran the search (Unipile account id).
    account_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    #: sha1 of the normalised filter set — see ``search_key_for``.
    search_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    #: LinkedIn's own id for the person, as returned by the search.
    provider_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    public_identifier: Mapped[Optional[str]] = mapped_column(String(255))
    profile_url: Mapped[Optional[str]] = mapped_column(String(512))

    name: Mapped[Optional[str]] = mapped_column(String(255))
    first_name: Mapped[Optional[str]] = mapped_column(String(255))
    headline: Mapped[Optional[str]] = mapped_column(Text)
    location: Mapped[Optional[str]] = mapped_column(String(255))
    company: Mapped[Optional[str]] = mapped_column(String(255))
    job_title: Mapped[Optional[str]] = mapped_column(String(255))
    #: 1 / 2 / 3 as LinkedIn reports it. 1 means a DM can go directly.
    network_distance: Mapped[Optional[str]] = mapped_column(String(30))
    picture_url: Mapped[Optional[str]] = mapped_column(String(1024))


class LinkedInSearchSend(Base, TimestampMixin):
    """The durable "this lead has been contacted" checkpoint."""

    __tablename__ = "linkedin_search_sends"
    __table_args__ = (
        # THE no-duplicates guarantee. Scoped by campaign so a genuinely
        # different message may reach the same person once, exactly like the
        # followers module.
        UniqueConstraint(
            "account_id",
            "lead_provider_id",
            "campaign_key",
            name="uq_search_send_account_lead_campaign",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    account_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    #: The person's LinkedIn id, not our row id: a lead row is per-search, and
    #: the same person found by two searches must still only be contacted once.
    lead_provider_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    #: sha1 of the normalised message text.
    campaign_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default=SearchSendStatus.CLAIMED, index=True, nullable=False
    )
    #: Which process holds the claim, so a claim left behind by a dead worker can
    #: be told apart from one a live worker is still working on.
    claimed_by: Mapped[Optional[str]] = mapped_column(String(64))
    #: "dm" when they were already connected, "invite" when an invitation went.
    reach: Mapped[Optional[str]] = mapped_column(String(20))

    message_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("linkedin_messages.id"), index=True
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error: Mapped[Optional[str]] = mapped_column(Text)
