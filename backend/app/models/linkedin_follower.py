"""Followers of a connected LinkedIn account, and the record of who was DM'd.

Two tables, deliberately separate because they have different cardinality:

``LinkedInFollower``
    The roster — one row per (connected account, follower). Refreshed from the
    provider; it is a cache of who follows the account, never outreach state.

``LinkedInFollowerSend``
    The checkpoint — one row per (account, follower, campaign). This is what
    guarantees a follower is never DM'd twice for the same outreach goal, and it
    survives restarts because the guarantee lives in a UNIQUE index rather than
    in a worker's memory. A row is inserted as a *claim* BEFORE the send is
    attempted, so a retried or concurrent request collides on the index instead
    of sending a second message.

Followers are intentionally NOT ``Contact`` rows: they arrive from LinkedIn with
only a name and headline, they are not discovered prospects, and mixing them in
would change the Prospects/Discover modules' data and counts.
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


class FollowerSendStatus:
    """Lifecycle of one follower's checkpoint row.

    ``CLAIMED`` is the crash-safety state: the claim was written, the send was
    started, and the outcome is unknown. It is never retried automatically —
    a possible duplicate is worse than a missed message — and is surfaced to the
    user as "needs review" instead.
    """

    CLAIMED = "claimed"
    SENT = "sent"
    FAILED = "failed"
    # Reachable by no available path (not connected, not an open profile, no
    # InMail). Retryable later, since connection state changes over time.
    SKIPPED = "skipped"

    #: Statuses a later run is allowed to attempt again.
    RETRYABLE = (FAILED, SKIPPED)


class LinkedInFollower(Base, TimestampMixin):
    __tablename__ = "linkedin_followers"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "provider_id", name="uq_linkedin_followers_account_provider"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Which of OUR connected LinkedIn accounts this person follows (Unipile
    # account_id). Followers are per-account: the same person can follow two.
    account_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # LinkedIn member id — the DM recipient address.
    provider_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    urn: Mapped[Optional[str]] = mapped_column(String(255))
    public_identifier: Mapped[Optional[str]] = mapped_column(String(255))

    name: Mapped[Optional[str]] = mapped_column(String(255))
    headline: Mapped[Optional[str]] = mapped_column(String(512))
    profile_url: Mapped[Optional[str]] = mapped_column(String(512))
    picture_url: Mapped[Optional[str]] = mapped_column(String(1024))

    first_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Updated on every sync, so a follower who has since unfollowed can be told
    # apart from a current one.
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)


class LinkedInFollowerSend(Base, TimestampMixin):
    __tablename__ = "linkedin_follower_sends"
    __table_args__ = (
        # The duplicate-prevention guarantee itself. Scoped per account AND per
        # campaign: the same follower may be reached again under a genuinely
        # different outreach goal, but never twice under the same one.
        UniqueConstraint(
            "account_id",
            "follower_provider_id",
            "campaign_key",
            name="uq_linkedin_follower_send",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    account_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # Denormalised from LinkedInFollower on purpose: the checkpoint must remain
    # valid even if the roster row is later re-synced or removed.
    follower_provider_id: Mapped[str] = mapped_column(
        String(255), index=True, nullable=False
    )
    # Stable hash of the message text — see linkedin_followers.campaign_key_for.
    campaign_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # The exact message body (before the per-follower greeting) as it was when
    # this send happened — the audit trail of what was actually said. Named
    # "goal" from when this module generated copy from an outreach goal; it now
    # holds the verbatim message the user typed.
    campaign_goal: Mapped[Optional[str]] = mapped_column(Text)

    follower_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("linkedin_followers.id"), index=True
    )
    message_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("linkedin_messages.id"), index=True
    )

    status: Mapped[str] = mapped_column(
        String(20), default=FollowerSendStatus.CLAIMED, index=True, nullable=False
    )
    # Which path delivered it: connected | open_profile | inmail.
    reach: Mapped[Optional[str]] = mapped_column(String(20))
    # Identifies the worker process that wrote a CLAIMED row, so a claim left
    # behind by a killed process is recognisable as interrupted on restart.
    claimed_by: Mapped[Optional[str]] = mapped_column(String(64))
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Only stamped once the provider confirms the send.
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text)
