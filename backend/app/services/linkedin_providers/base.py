"""LinkedIn outreach provider interface.

Mirrors the email/voice provider pattern: a pluggable backend selected by
LINKEDIN_PROVIDER. The ``stub`` provider never touches the network so the
draft → approve → send workflow can run safely with no real account.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class LinkedInProfile:
    """A resolved LinkedIn profile (from a public identifier or URL)."""

    found: bool
    provider_id: Optional[str] = None
    public_identifier: Optional[str] = None
    network_distance: Optional[str] = None  # FIRST_DEGREE | SECOND_DEGREE | ...
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    headline: Optional[str] = None
    is_premium: bool = False
    is_open_profile: bool = False
    error: Optional[str] = None
    # True when `error` is a transport/connectivity failure (provider
    # unreachable) rather than "not found" — callers use this to stop hammering
    # a dead endpoint instead of retrying every remaining message.
    network_error: bool = False

    @property
    def is_connected(self) -> bool:
        """True when we can DM directly (1st-degree connection)."""
        return (self.network_distance or "").upper() in ("FIRST_DEGREE", "DISTANCE_1")


@dataclass
class SendResult:
    sent: bool
    provider: str
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    error: Optional[str] = None
    # True when the provider says this person cannot be messaged at all on this
    # path (LinkedIn "user_unreachable"): not a connection, not an open profile,
    # and no InMail available. A permanent-for-now condition, not a transport
    # failure — callers skip them cleanly instead of counting a send failure.
    unreachable: bool = False


@dataclass
class FollowerRecord:
    """One follower of a connected LinkedIn account."""

    # LinkedIn member id, directly usable as a message recipient.
    provider_id: str
    urn: Optional[str] = None
    name: Optional[str] = None
    headline: Optional[str] = None
    profile_url: Optional[str] = None
    picture_url: Optional[str] = None


@dataclass
class FollowerPage:
    """One page of followers. ``cursor`` is None on the last page."""

    followers: list["FollowerRecord"] = field(default_factory=list)
    cursor: Optional[str] = None
    supported: bool = True
    error: Optional[str] = None
    network_error: bool = False


@dataclass
class InviteResult:
    sent: bool
    provider: str
    invitation_id: Optional[str] = None
    # True when the invite was rejected because they are already connected —
    # the caller should fall back to sending a direct message.
    already_connected: bool = False
    error: Optional[str] = None


@dataclass
class ReplyResult:
    found: bool
    received_at: Optional[datetime] = None
    snippet: Optional[str] = None
    body: Optional[str] = None
    from_name: Optional[str] = None
    error: Optional[str] = None
    network_error: bool = False


class LinkedInProvider(ABC):
    name = "base"

    @abstractmethod
    def resolve_profile(self, identifier: str) -> LinkedInProfile:
        """Resolve a public identifier or LinkedIn URL to a profile + provider_id."""
        ...

    @abstractmethod
    def send_message(
        self, *, provider_id: str, text: str, inmail: bool = False
    ) -> SendResult:
        """Start a new chat (or reuse an existing one) and send a direct message.

        ``inmail`` opens the conversation as an InMail, the only way to reach
        someone who is neither a 1st-degree connection nor an open profile. It
        consumes an InMail credit, so callers should only set it after the
        cheaper paths are ruled out. Defaults to False — every existing caller
        keeps exactly its current behaviour.
        """
        ...

    @abstractmethod
    def send_invitation(self, *, provider_id: str, note: str) -> InviteResult:
        """Send a connection request with an optional note (<= 300 chars)."""
        ...

    def is_connected(self, *, provider_id: str, identifier: Optional[str] = None) -> bool:
        """Best-effort check whether the account is a 1st-degree connection."""
        return False

    def supports_tracking(self) -> bool:
        return False

    def supports_followers(self) -> bool:
        """True when this provider can enumerate the account's followers."""
        return False

    def list_followers(
        self, *, cursor: Optional[str] = None, limit: int = 100
    ) -> FollowerPage:
        """One page of the connected account's own followers.

        Note this source is capped by LinkedIn at 1,000 records — see the Unipile
        implementation. ``list_connections`` is the uncapped alternative.
        """
        return FollowerPage(
            supported=False, error="Listing followers is not supported by this provider."
        )

    def list_connections(
        self,
        *,
        cursor: Optional[str] = None,
        limit: int = 100,
        offset: Optional[int] = None,
    ) -> FollowerPage:
        """One page of the account's 1st-degree connections.

        ``offset`` fetches a page directly without walking the ones before it,
        which lets callers pull pages concurrently. Providers that cannot do that
        may ignore it and page sequentially.

        Same shape as ``list_followers`` so callers can use either as an audience
        source. Connections are the better one in practice: LinkedIn caps the
        followers list at 1,000 but pages connections all the way through, and
        connecting auto-follows, so they are very nearly the same people — all of
        them directly messageable without an InMail credit.
        """
        return FollowerPage(
            supported=False,
            error="Listing connections is not supported by this provider.",
        )

    def check_reply(
        self, *, chat_id: Optional[str], provider_id: str, since: datetime
    ) -> ReplyResult:
        return ReplyResult(found=False, error="Reply tracking not supported.")


_PUBLIC_ID_RE = re.compile(r"/in/([^/?#]+)", re.IGNORECASE)


def public_identifier_from_url(url_or_id: str) -> str:
    """Extract a LinkedIn public identifier from a profile URL (or pass through).

    "https://www.linkedin.com/in/williamhgates/" -> "williamhgates"
    "williamhgates" -> "williamhgates"
    """
    value = (url_or_id or "").strip()
    if not value:
        return ""
    match = _PUBLIC_ID_RE.search(value)
    if match:
        return match.group(1).strip().rstrip("/")
    # A LinkedIn URL that is NOT a personal /in/ profile (company, school,
    # showcase, posts, jobs, …) cannot be messaged — treat as no identifier so
    # callers skip it instead of sending a garbage identifier to the API.
    low = value.lower()
    if low.startswith("http") or "linkedin.com" in low or "/" in value:
        return ""
    # Otherwise assume it's already a bare public identifier.
    return value.rstrip("/")
