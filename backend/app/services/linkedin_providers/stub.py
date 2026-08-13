"""Stub LinkedIn provider — never sends; returns deterministic fake data.

Safe default so the draft/approve/send workflow works with no Unipile account.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from app.services.linkedin_providers.base import (
    FollowerPage,
    FollowerRecord,
    InviteResult,
    LinkedInProfile,
    LinkedInProvider,
    ReplyResult,
    SendResult,
    public_identifier_from_url,
)

# Deterministic fake followers, so the Followers workflow (sync -> draft ->
# approve -> send -> checkpoint) can be exercised with no Unipile account.
_STUB_FOLLOWERS = [
    ("Ayesha Khan", "VP Clinical Operations at Novara Bio"),
    ("Daniel Okafor", "Founder & CEO, Helix Diagnostics"),
    ("Priya Raman", "Head of Regulatory Affairs | Biotech"),
    ("Marcus Feld", "Partner at Grantham Life Sciences"),
    ("Lena Vasquez", "Chief Medical Officer at Corvid Therapeutics"),
    ("Tom Baird", "Board Director | Former CEO, Ardent Pharma"),
]


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

    def send_message(
        self, *, provider_id: str, text: str, inmail: bool = False
    ) -> SendResult:
        return SendResult(
            sent=True,
            provider=self.name,
            chat_id=f"stub-chat-{uuid.uuid4().hex[:8]}",
            message_id=f"stub-msg-{uuid.uuid4().hex[:8]}",
        )

    def supports_followers(self) -> bool:
        return True

    def list_followers(
        self, *, cursor: Optional[str] = None, limit: int = 100
    ) -> FollowerPage:
        """A single fixed page of fake people (stable ids across calls, so
        re-syncing updates the same rows instead of inventing new people)."""
        if cursor:
            return FollowerPage(followers=[], cursor=None)
        followers = [
            FollowerRecord(
                provider_id=f"stub-follower-{index}",
                urn=f"urn:li:person:stub-{index}",
                name=name,
                headline=headline,
                profile_url=f"https://www.linkedin.com/in/stub-follower-{index}/",
            )
            for index, (name, headline) in enumerate(_STUB_FOLLOWERS[:limit], start=1)
        ]
        return FollowerPage(followers=followers, cursor=None)

    def list_connections(
        self,
        *,
        cursor: Optional[str] = None,
        limit: int = 100,
        offset: Optional[int] = None,
    ) -> FollowerPage:
        """Same fake people as ``list_followers``.

        Identical ids on purpose: the live endpoints return the same member ids
        for the same person, so a stub that invented a second set of people would
        hide dedup bugs rather than expose them. A non-zero ``offset`` is past the
        end of the fixed list, so it returns nothing — which is exactly how the
        parallel fetcher detects the tail.
        """
        if offset:
            return FollowerPage(followers=[], cursor=None)
        return self.list_followers(cursor=cursor, limit=limit)

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
