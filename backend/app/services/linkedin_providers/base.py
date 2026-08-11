"""LinkedIn outreach provider interface.

Mirrors the email/voice provider pattern: a pluggable backend selected by
LINKEDIN_PROVIDER. The ``stub`` provider never touches the network so the
draft → approve → send workflow can run safely with no real account.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
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
    def send_message(self, *, provider_id: str, text: str) -> SendResult:
        """Start a new chat (or reuse an existing one) and send a direct message."""
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
