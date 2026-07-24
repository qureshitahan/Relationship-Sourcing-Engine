"""Stub LinkedIn provider — never sends; returns deterministic fake data.

Safe default so the draft/approve/send workflow works with no Unipile account.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from app.services.linkedin_providers.base import (
    InviteResult,
    LinkedInProfile,
    LinkedInProvider,
    ReplyResult,
    SendResult,
    public_identifier_from_url,
)


class StubLinkedInProvider(LinkedInProvider):
    name = "stub"

    def resolve_profile(self, identifier: str) -> LinkedInProfile:
        pid = public_identifier_from_url(identifier)
        if not pid:
            return LinkedInProfile(found=False, error="No LinkedIn identifier provided.")
        return LinkedInProfile(
            found=True,
            provider_id=f"stub-{pid}",
            public_identifier=pid,
            network_distance="SECOND_DEGREE",
        )

    def send_message(self, *, provider_id: str, text: str) -> SendResult:
        return SendResult(
            sent=True,
            provider=self.name,
            chat_id=f"stub-chat-{uuid.uuid4().hex[:8]}",
            message_id=f"stub-msg-{uuid.uuid4().hex[:8]}",
        )

    def send_invitation(self, *, provider_id: str, note: str) -> InviteResult:
        return InviteResult(
            sent=True,
            provider=self.name,
            invitation_id=f"stub-inv-{uuid.uuid4().hex[:8]}",
        )

    def is_connected(self, *, provider_id: str, identifier: Optional[str] = None) -> bool:
        return False

    def supports_tracking(self) -> bool:
        return False

    def check_reply(
        self, *, chat_id: Optional[str], provider_id: str, since: datetime
    ) -> ReplyResult:
        return ReplyResult(found=False)
