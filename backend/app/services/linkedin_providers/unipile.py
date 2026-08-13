"""Send LinkedIn messages + connection invitations via Unipile.

Unipile exposes a unified messaging API over a per-tenant DSN. This provider
covers the LinkedIn flow the app needs:

  * resolve a public identifier / profile URL -> provider_id + connection degree
  * send a direct message (1st-degree connections)
  * send a connection invitation with a note (everyone else)
  * poll a chat for replies; re-resolve a profile to detect accepted invites

Endpoints (verified against api28.unipile.com):
  GET  /api/v1/users/{identifier}?account_id=...      -> profile (provider_id, network_distance)
  POST /api/v1/chats            (multipart)           -> start chat + first message
  POST /api/v1/chats/{id}/messages (multipart)        -> reply in a chat
  POST /api/v1/users/invite     (json)                -> connection request
  GET  /api/v1/chats/{id}/messages?...                -> messages (reply detection)

Docs: https://developer.unipile.com/docs/getting-started
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.core.config import settings
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

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30.0

# Largest followers page Unipile actually accepts. The API reference says 100 for
# your own profile, but anything above 50 is rejected outright with
# 400 errors/limit_too_high (measured against api28: 60/80/90/100 all fail, 50
# succeeds). Sending 100 made every sync fail with zero followers imported.
FOLLOWERS_PAGE_LIMIT = 50


def cursor_for_offset(offset: int, limit: int = FOLLOWERS_PAGE_LIMIT) -> str:
    """Build a pagination cursor for an arbitrary offset.

    Unipile's cursor is not opaque: it is base64 of ``{"limit":N,"startIndex":M}``.
    Synthesising it means a page can be fetched WITHOUT first walking every page
    before it, which is what lets the roster sync run pages concurrently instead
    of one 2-second round-trip at a time.

    Verified against the live API: a synthesised cursor for startIndex=100
    returned byte-identical member ids to the cursor obtained by walking there.
    """
    payload = json.dumps({"limit": int(limit), "startIndex": int(offset)}, separators=(",", ":"))
    return base64.b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _is_unreachable(status_code: int, body: str) -> bool:
    """True when LinkedIn refused the message because the person can't be reached.

    Unipile answers 422 ``user_unreachable`` when the recipient is neither a
    connection nor an open profile and no InMail is available. That is a clean
    "skip this person", not a failure worth retrying — every other error is.
    """
    if status_code != 422:
        return False
    low = (body or "").lower()
    return "unreachable" in low or "cannot_resend" in low or "insufficient" in low


class UnipileLinkedInProvider(LinkedInProvider):
    name = "unipile"

    def __init__(self, account_id: Optional[str] = None) -> None:
        self.api_key = (settings.unipile_api_key or "").strip()
        # Active account may be overridden (e.g. a user-selected connected account).
        self.account_id = (account_id or settings.unipile_account_id or "").strip()
        dsn = (settings.unipile_dsn or "").strip().rstrip("/")
        # Accept a bare host:port or a full URL.
        if dsn.startswith("http"):
            self.api_root = dsn
        else:
            self.api_root = f"https://{dsn}" if dsn else ""
        self.base_url = f"{self.api_root}/api/v1" if self.api_root else ""

    # --- helpers ---

    def _configured(self) -> bool:
        return bool(self.api_key and self.account_id and self.base_url)

    def _headers(self, *, json: bool = False) -> dict:
        h = {"X-API-KEY": self.api_key, "accept": "application/json"}
        if json:
            h["content-type"] = "application/json"
        return h

    @staticmethod
    def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
        if not raw:
            return None
        try:
            text = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except (ValueError, TypeError):
            return None

    # --- profile ---

    def resolve_profile(self, identifier: str) -> LinkedInProfile:
        if not self._configured():
            return LinkedInProfile(found=False, error="Unipile not configured.")
        ident = public_identifier_from_url(identifier)
        if not ident:
            return LinkedInProfile(found=False, error="No LinkedIn identifier provided.")
        url = f"{self.base_url}/users/{ident}"
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.get(
                    url, headers=self._headers(), params={"account_id": self.account_id}
                )
        except httpx.HTTPError as exc:
            logger.warning("Unipile resolve_profile network error: %s", exc)
            return LinkedInProfile(found=False, error=str(exc), network_error=True)
        if resp.status_code == 404:
            return LinkedInProfile(found=False, error="LinkedIn profile not found.")
        if resp.status_code >= 400:
            logger.warning(
                "Unipile resolve_profile failed (%s): %s", resp.status_code, resp.text[:300]
            )
            return LinkedInProfile(
                found=False, error=f"Unipile {resp.status_code}: {resp.text[:200]}"
            )
        d = resp.json() or {}
        return LinkedInProfile(
            found=bool(d.get("provider_id")),
            provider_id=d.get("provider_id"),
            public_identifier=d.get("public_identifier") or ident,
            network_distance=d.get("network_distance"),
            first_name=d.get("first_name"),
            last_name=d.get("last_name"),
            headline=d.get("headline"),
            is_premium=bool(d.get("is_premium")),
            is_open_profile=bool(d.get("is_open_profile")),
        )

    def is_connected(self, *, provider_id: str, identifier: Optional[str] = None) -> bool:
        profile = self.resolve_profile(identifier or provider_id)
        return profile.found and profile.is_connected

    # --- sending ---

    def send_message(
        self, *, provider_id: str, text: str, inmail: bool = False
    ) -> SendResult:
        if not self._configured():
            return SendResult(sent=False, provider=self.name, error="Unipile not configured.")
        # /chats requires multipart/form-data; (None, value) tuples send plain
        # form fields (no filename). One attendees_ids field = one recipient.
        files = [
            ("account_id", (None, self.account_id)),
            ("attendees_ids", (None, provider_id)),
            ("text", (None, text)),
        ]
        if inmail:
            # Nested options travel as BRACKETED form fields, not a JSON blob.
            # Sending {"api":"classic","inmail":true} as one `linkedin` field is
            # rejected with 400 errors/invalid_parameters ("Extra fields for
            # Linkedin products"), which silently failed every InMail send;
            # `linkedin[api]` + `linkedin[inmail]` passes schema validation.
            # Only added when asked for, so an ordinary DM's body is unchanged.
            files.append(("linkedin[api]", (None, "classic")))
            files.append(("linkedin[inmail]", (None, "true")))
        url = f"{self.base_url}/chats"
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.post(url, headers=self._headers(), files=files)
        except httpx.HTTPError as exc:
            logger.warning("Unipile send_message network error: %s", exc)
            return SendResult(sent=False, provider=self.name, error=str(exc))
        if resp.status_code not in (200, 201):
            logger.warning(
                "Unipile start chat failed (%s): %s", resp.status_code, resp.text[:300]
            )
            return SendResult(
                sent=False, provider=self.name,
                error=f"Unipile {resp.status_code}: {resp.text[:200]}",
                unreachable=_is_unreachable(resp.status_code, resp.text),
            )
        d = resp.json() or {}
        return SendResult(
            sent=True,
            provider=self.name,
            chat_id=d.get("chat_id") or d.get("id"),
            message_id=d.get("message_id") or d.get("id"),
        )

    def send_message_in_chat(self, *, chat_id: str, text: str) -> SendResult:
        if not self._configured():
            return SendResult(sent=False, provider=self.name, error="Unipile not configured.")
        files = [("text", (None, text))]
        url = f"{self.base_url}/chats/{chat_id}/messages"
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.post(url, headers=self._headers(), files=files)
        except httpx.HTTPError as exc:
            return SendResult(sent=False, provider=self.name, error=str(exc))
        if resp.status_code not in (200, 201):
            return SendResult(
                sent=False, provider=self.name,
                error=f"Unipile {resp.status_code}: {resp.text[:200]}",
            )
        d = resp.json() or {}
        return SendResult(
            sent=True, provider=self.name, chat_id=chat_id,
            message_id=d.get("message_id") or d.get("id"),
        )

    def send_invitation(self, *, provider_id: str, note: str) -> InviteResult:
        if not self._configured():
            return InviteResult(sent=False, provider=self.name, error="Unipile not configured.")
        payload = {"account_id": self.account_id, "provider_id": provider_id}
        if note:
            payload["message"] = note[:300]
        url = f"{self.base_url}/users/invite"
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.post(url, headers=self._headers(json=True), json=payload)
        except httpx.HTTPError as exc:
            logger.warning("Unipile send_invitation network error: %s", exc)
            return InviteResult(sent=False, provider=self.name, error=str(exc))
        if resp.status_code in (200, 201):
            d = resp.json() or {}
            return InviteResult(
                sent=True, provider=self.name,
                invitation_id=d.get("invitation_id") or d.get("id"),
            )
        body = resp.text or ""
        if "already" in body.lower() and "connect" in body.lower():
            return InviteResult(sent=False, provider=self.name, already_connected=True,
                                error="Already connected.")
        logger.warning("Unipile invite failed (%s): %s", resp.status_code, body[:300])
        return InviteResult(
            sent=False, provider=self.name,
            error=f"Unipile {resp.status_code}: {body[:200]}",
        )

    # --- followers ---

    def supports_followers(self) -> bool:
        return self._configured()

    def list_followers(
        self, *, cursor: Optional[str] = None, limit: int = FOLLOWERS_PAGE_LIMIT
    ) -> FollowerPage:
        """One page of the connected account's OWN followers.

        No ``user_id`` is sent, which is what makes this the account's own
        follower list rather than some other profile's. Pagination is by
        ``cursor``; the last page comes back without one.
        """
        if not self._configured():
            return FollowerPage(supported=False, error="Unipile not configured.")
        params: dict = {
            "account_id": self.account_id,
            "limit": max(1, min(int(limit), FOLLOWERS_PAGE_LIMIT)),
        }
        if cursor:
            params["cursor"] = cursor
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.get(
                    f"{self.base_url}/users/followers", headers=self._headers(), params=params
                )
        except httpx.HTTPError as exc:
            logger.warning("Unipile list_followers network error: %s", exc)
            return FollowerPage(error=str(exc), network_error=True)
        if resp.status_code >= 400:
            logger.warning(
                "Unipile list_followers failed (%s): %s", resp.status_code, resp.text[:300]
            )
            return FollowerPage(
                error=f"Unipile {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json() or {}
        items = data.get("items") or data.get("data") or []
        followers: list[FollowerRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            provider_id = (item.get("id") or item.get("provider_id") or "").strip()
            if not provider_id:
                # Without a member id there is nobody to address the DM to.
                continue
            followers.append(
                FollowerRecord(
                    provider_id=provider_id,
                    urn=item.get("urn"),
                    name=(item.get("name") or "").strip() or None,
                    headline=(item.get("headline") or "").strip() or None,
                    profile_url=item.get("profile_url") or item.get("public_profile_url"),
                    picture_url=item.get("profile_picture_url"),
                )
            )
        return FollowerPage(followers=followers, cursor=data.get("cursor") or None)

    def list_connections(
        self,
        *,
        cursor: Optional[str] = None,
        limit: int = FOLLOWERS_PAGE_LIMIT,
        offset: Optional[int] = None,
    ) -> FollowerPage:
        """One page of the account's 1st-degree connections.

        This is the audience source the Followers module uses, because
        ``/users/followers`` is hard-capped by LinkedIn at 1,000 records while
        this one pages all the way through. Measured on an account with 7,759
        followers / 7,533 connections: followers stopped dead at exactly 1,000
        (cursor gone), connections were still paging past 1,594.

        Returns the same ``FollowerPage`` shape as ``list_followers`` so either can
        be swapped in. Field names differ from the followers endpoint —
        ``member_id`` rather than ``id``, and a split first/last name — but the
        member id is the same ACoAA… identifier, so rows dedupe against followers
        already synced.
        """
        if not self._configured():
            return FollowerPage(supported=False, error="Unipile not configured.")
        page_limit = max(1, min(int(limit), FOLLOWERS_PAGE_LIMIT))
        params: dict = {"account_id": self.account_id, "limit": page_limit}
        # An explicit offset jumps straight to that page (see cursor_for_offset);
        # an explicit cursor always wins, so sequential callers are unaffected.
        if cursor:
            params["cursor"] = cursor
        elif offset:
            params["cursor"] = cursor_for_offset(offset, page_limit)
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.get(
                    f"{self.base_url}/users/relations", headers=self._headers(), params=params
                )
        except httpx.HTTPError as exc:
            logger.warning("Unipile list_connections network error: %s", exc)
            return FollowerPage(error=str(exc), network_error=True)
        if resp.status_code >= 400:
            logger.warning(
                "Unipile list_connections failed (%s): %s", resp.status_code, resp.text[:300]
            )
            return FollowerPage(error=f"Unipile {resp.status_code}: {resp.text[:200]}")
        data = resp.json() or {}
        items = data.get("items") or data.get("data") or []
        connections: list[FollowerRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            provider_id = (item.get("member_id") or item.get("id") or "").strip()
            if not provider_id:
                # Without a member id there is nobody to address the DM to.
                continue
            name = " ".join(
                p for p in (item.get("first_name"), item.get("last_name")) if p
            ).strip()
            connections.append(
                FollowerRecord(
                    provider_id=provider_id,
                    urn=item.get("member_urn") or item.get("connection_urn"),
                    name=name or None,
                    headline=(item.get("headline") or "").strip() or None,
                    profile_url=item.get("public_profile_url") or item.get("profile_url"),
                    picture_url=item.get("profile_picture_url"),
                )
            )
        return FollowerPage(followers=connections, cursor=data.get("cursor") or None)

    # --- account management (connect / list) ---

    def list_accounts(self) -> list[dict]:
        """List LinkedIn accounts connected to this Unipile tenant."""
        if not (self.api_key and self.base_url):
            return []
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.get(f"{self.base_url}/accounts", headers=self._headers())
        except httpx.HTTPError as exc:
            logger.warning("Unipile list_accounts error: %s", exc)
            return []
        if resp.status_code >= 400:
            logger.warning("Unipile list_accounts failed (%s)", resp.status_code)
            return []
        data = resp.json() or {}
        items = data.get("items") or data.get("data") or []
        out = []
        for a in items:
            if not isinstance(a, dict):
                continue
            if (a.get("type") or "").upper() not in ("", "LINKEDIN"):
                continue
            sources = a.get("sources") or []
            status = "OK"
            if isinstance(sources, list) and sources:
                status = sources[0].get("status", "OK")
            out.append(
                {
                    "id": a.get("id"),
                    "name": a.get("name") or a.get("username"),
                    "type": a.get("type"),
                    "status": status,
                }
            )
        return out

    def create_hosted_auth_link(
        self,
        *,
        name: str,
        success_redirect_url: Optional[str] = None,
        failure_redirect_url: Optional[str] = None,
        notify_url: Optional[str] = None,
        expires_minutes: int = 60,
    ) -> tuple[Optional[str], Optional[str]]:
        """Create a Unipile hosted-auth link to connect a LinkedIn account.

        Returns (url, error). Unipile's wizard handles login + 2FA/CAPTCHA, so no
        credentials ever pass through this app.
        """
        if not (self.api_key and self.api_root):
            return None, "Unipile not configured."
        expires = (datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        payload: dict = {
            "type": "create",
            "providers": ["LINKEDIN"],
            "api_url": self.api_root,
            "expiresOn": expires,
            "name": name,
        }
        if success_redirect_url:
            payload["success_redirect_url"] = success_redirect_url
        if failure_redirect_url:
            payload["failure_redirect_url"] = failure_redirect_url
        if notify_url:
            payload["notify_url"] = notify_url
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.post(
                    f"{self.base_url}/hosted/accounts/link",
                    headers=self._headers(json=True),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            logger.warning("Unipile hosted link error: %s", exc)
            return None, str(exc)
        if resp.status_code not in (200, 201):
            logger.warning("Unipile hosted link failed (%s): %s", resp.status_code, resp.text[:300])
            return None, f"Unipile {resp.status_code}: {resp.text[:200]}"
        return (resp.json() or {}).get("url"), None

    # --- reply tracking ---

    def supports_tracking(self) -> bool:
        return self._configured()

    def check_reply(
        self, *, chat_id: Optional[str], provider_id: str, since: datetime
    ) -> ReplyResult:
        if not self._configured():
            return ReplyResult(found=False, error="Unipile not configured.")
        if not chat_id:
            return ReplyResult(found=False)
        url = f"{self.base_url}/chats/{chat_id}/messages"
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.get(url, headers=self._headers(), params={"limit": 20})
        except httpx.HTTPError as exc:
            logger.warning("Unipile check_reply network error: %s", exc)
            return ReplyResult(found=False, error=str(exc), network_error=True)
        if resp.status_code >= 400:
            return ReplyResult(
                found=False, error=f"Unipile {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json() or {}
        messages = data.get("items") or data.get("data") or []
        # Newest inbound message (not sent by us) after `since`.
        best: Optional[dict] = None
        best_dt: Optional[datetime] = None
        for msg in messages:
            # is_sender truthy = message we sent; skip our own.
            if msg.get("is_sender") in (True, 1, "1"):
                continue
            dt = self._parse_dt(msg.get("timestamp") or msg.get("date"))
            if dt is not None and dt < since:
                continue
            if best_dt is None or (dt and dt > best_dt):
                best, best_dt = msg, dt
        if best is None:
            return ReplyResult(found=False)
        text = (best.get("text") or "").strip()
        return ReplyResult(
            found=True,
            received_at=best_dt,
            snippet=text[:500] or None,
            body=text or None,
            from_name=(best.get("sender_name") or None),
        )
