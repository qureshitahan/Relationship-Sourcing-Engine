"""Email provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class SendResult:
    sent: bool
    provider: str
    message_id: Optional[str] = None
    error: Optional[str] = None
    # Graph identifiers captured at send time, used later to match replies.
    conversation_id: Optional[str] = None
    internet_message_id: Optional[str] = None
    # True when the message was handed to the mail server for deferred delivery
    # at ``send_at`` (it will be delivered even if this app is offline) rather
    # than transmitted immediately.
    scheduled: bool = False
    # Provider-side resource id for a deferred message, used to cancel it later.
    remote_message_id: Optional[str] = None


@dataclass
class ReplyResult:
    """A detected inbound reply to a previously sent email."""

    found: bool
    received_at: Optional[datetime] = None
    snippet: Optional[str] = None
    # Full plain-text body of the reply, for the in-app conversation thread.
    body: Optional[str] = None
    from_name: Optional[str] = None
    error: Optional[str] = None


class EmailProvider(ABC):
    name = "base"

    @abstractmethod
    def send(
        self,
        *,
        to_email: str,
        subject: str,
        body: str,
        from_email: str,
        from_name: str,
        html_body: Optional[str] = None,
        send_at: Optional[datetime] = None,
    ) -> SendResult:
        """Send a message. When ``send_at`` is provided and the provider
        supports deferred delivery, the message is queued on the mail server
        to be delivered at that UTC time (and SendResult.scheduled is True)."""
        ...

    # --- Scheduled (deferred) delivery (optional) ---
    def supports_scheduled_send(self) -> bool:
        """True if the provider can defer delivery server-side via ``send_at``."""
        return False

    def cancel_scheduled(self, *, remote_message_id: str) -> bool:
        """Cancel a previously deferred message. Returns True on success."""
        return False

    # --- Reply tracking (optional; only real providers implement it) ---
    def supports_reply_tracking(self) -> bool:
        return False

    def check_reply(
        self,
        *,
        to_email: str,
        since: datetime,
        conversation_id: Optional[str] = None,
    ) -> ReplyResult:
        """Look for a reply from ``to_email`` after ``since``. Default: unsupported."""
        return ReplyResult(found=False, error="Reply tracking not supported by this provider.")
