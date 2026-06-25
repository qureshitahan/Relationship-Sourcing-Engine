"""Stub email provider — does not send; returns a fake message id."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from app.services.email_providers.base import EmailProvider, SendResult


class StubEmailProvider(EmailProvider):
    name = "stub"

    def send(
        self,
        *,
        to_email: str,
        subject: str,
        body: str,
        from_email: str,
        from_name: str,
        html_body: str | None = None,
        send_at: Optional[datetime] = None,
    ) -> SendResult:
        # Intentionally does NOT send anything. Safe default for the MVP.
        return SendResult(
            sent=True,
            provider=self.name,
            message_id=f"stub-{uuid.uuid4().hex[:12]}",
            scheduled=send_at is not None,
        )
