"""Send outreach via Microsoft 365 / Outlook using the Graph API.

Designed for sending as a specific mailbox (e.g. dalbir.bains@galaxypharma.net)
after Galaxy IT registers an Azure app and grants Mail.Send (application).

Setup (one-time, Galaxy IT / Serge):
  1. Azure Portal → App registrations → New registration
  2. API permissions → Microsoft Graph → Application → Mail.Send
     (add Mail.Read as well to enable reply tracking — Phase 6b)
  3. Grant admin consent for the tenant
  4. Certificates & secrets → New client secret → copy value
  5. Set MICROSOFT_TENANT_ID, MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET,
     and MICROSOFT_SEND_AS_USER in backend/.env

Docs: https://learn.microsoft.com/en-us/graph/api/user-sendmail
      https://learn.microsoft.com/en-us/graph/api/user-list-messages
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import httpx

from app.core.config import settings
from app.services.email_providers.base import EmailProvider, ReplyResult, SendResult

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
REQUEST_TIMEOUT = 30.0


class MicrosoftGraphEmailProvider(EmailProvider):
    name = "microsoft_graph"

    def __init__(self, send_as_user: Optional[str] = None) -> None:
        self.tenant_id = settings.microsoft_tenant_id
        self.client_id = settings.microsoft_client_id
        self.client_secret = settings.microsoft_client_secret
        # Per-mailbox UPN when provided (multi-sender); the single Azure app can
        # send as any mailbox in its tenant. Falls back to the global setting.
        self.send_as_user = (
            send_as_user
            or settings.microsoft_send_as_user
            or settings.outreach_from_email
        )
        self._access_token: Optional[str] = None

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
        if not self.tenant_id or not self.client_id or not self.client_secret:
            return SendResult(
                sent=False,
                provider=self.name,
                error=(
                    "Microsoft Graph not configured. Set MICROSOFT_TENANT_ID, "
                    "MICROSOFT_CLIENT_ID, and MICROSOFT_CLIENT_SECRET in .env."
                ),
            )
        if not self.send_as_user:
            return SendResult(
                sent=False,
                provider=self.name,
                error="MICROSOFT_SEND_AS_USER (or OUTREACH_FROM_EMAIL) is required.",
            )

        token = self._get_access_token()
        if not token:
            return SendResult(
                sent=False,
                provider=self.name,
                error="Failed to obtain Microsoft Graph access token.",
            )

        # When html_body is provided (open tracking on), send as HTML.
        message_body = (
            {"contentType": "HTML", "content": html_body}
            if html_body
            else {"contentType": "Text", "content": body}
        )

        # Deferred (scheduled) delivery: create a draft with the Outlook
        # "deferred send time" extended property, then send it. Exchange holds
        # the message and delivers it at send_at — independent of this app.
        if send_at is not None:
            return self._send_deferred(
                token=token,
                subject=subject,
                message_body=message_body,
                to_email=to_email,
                send_at=send_at,
            )

        # Mail is sent from the mailbox identified by send_as_user (UPN).
        payload = {
            "message": {
                "subject": subject,
                "body": message_body,
                "toRecipients": [{"emailAddress": {"address": to_email}}],
            },
            "saveToSentItems": True,
        }

        url = f"{GRAPH_BASE}/users/{self.send_as_user}/sendMail"
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            logger.warning("Microsoft Graph sendMail network error: %s", exc)
            return SendResult(sent=False, provider=self.name, error=str(exc))

        if resp.status_code == 202:
            # sendMail returns no body. Best-effort: look up the message we just
            # placed in Sent Items to capture its conversationId for reply
            # matching later (requires Mail.Read; ignored if unavailable).
            conv_id, imsg_id = self._lookup_sent_message(token, subject, to_email)
            return SendResult(
                sent=True,
                provider=self.name,
                message_id=imsg_id or f"graph-{to_email}",
                conversation_id=conv_id,
                internet_message_id=imsg_id,
            )

        logger.warning(
            "Microsoft Graph sendMail failed (%s): %s",
            resp.status_code,
            resp.text[:400],
        )
        return SendResult(
            sent=False,
            provider=self.name,
            error=f"Graph API {resp.status_code}: {resp.text[:300]}",
        )

    # --- Scheduled (deferred) delivery via Outlook ---

    def supports_scheduled_send(self) -> bool:
        return bool(
            self.tenant_id and self.client_id and self.client_secret and self.send_as_user
        )

    def _send_deferred(
        self,
        *,
        token: str,
        subject: str,
        message_body: dict,
        to_email: str,
        send_at: datetime,
    ) -> SendResult:
        """Create a draft with PidTagDeferredSendTime, then submit it.

        Requires the Azure app to have Mail.ReadWrite (create/send draft) in
        addition to Mail.Send. Exchange delivers at ``send_at`` server-side.
        """
        # PidTagDeferredSendTime: property tag 0x3FEF, type SystemTime. Value is
        # ISO-8601 UTC. Exchange holds the message until this time.
        when_utc = send_at
        if when_utc.tzinfo is not None:
            from datetime import timezone

            when_utc = when_utc.astimezone(timezone.utc).replace(tzinfo=None)
        deferred_value = when_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        draft_payload = {
            "subject": subject,
            "body": message_body,
            "toRecipients": [{"emailAddress": {"address": to_email}}],
            "singleValueExtendedProperties": [
                {"id": "SystemTime 0x3FEF", "value": deferred_value}
            ],
        }
        base = f"{GRAPH_BASE}/users/{self.send_as_user}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                create = client.post(base, headers=headers, json=draft_payload)
                if create.status_code not in (200, 201):
                    logger.warning(
                        "Graph create deferred draft failed (%s): %s",
                        create.status_code,
                        create.text[:300],
                    )
                    return SendResult(
                        sent=False,
                        provider=self.name,
                        error=f"Graph API {create.status_code}: {create.text[:300]}",
                    )
                msg = create.json() or {}
                msg_id = msg.get("id")
                conv_id = msg.get("conversationId")
                imsg_id = msg.get("internetMessageId")

                send_resp = client.post(f"{base}/{msg_id}/send", headers=headers)
                if send_resp.status_code not in (202, 200):
                    logger.warning(
                        "Graph send deferred draft failed (%s): %s",
                        send_resp.status_code,
                        send_resp.text[:300],
                    )
                    return SendResult(
                        sent=False,
                        provider=self.name,
                        error=f"Graph API {send_resp.status_code}: {send_resp.text[:300]}",
                    )
        except httpx.HTTPError as exc:
            logger.warning("Microsoft Graph deferred send network error: %s", exc)
            return SendResult(sent=False, provider=self.name, error=str(exc))

        return SendResult(
            sent=True,
            provider=self.name,
            scheduled=True,
            message_id=imsg_id or msg_id or f"graph-{to_email}",
            conversation_id=conv_id,
            internet_message_id=imsg_id,
            remote_message_id=msg_id,
        )

    def cancel_scheduled(self, *, remote_message_id: str) -> bool:
        """Delete a deferred message before its send time to cancel delivery."""
        if not self.supports_scheduled_send():
            return False
        token = self._get_access_token()
        if not token:
            return False
        url = f"{GRAPH_BASE}/users/{self.send_as_user}/messages/{remote_message_id}"
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.delete(url, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            logger.warning("Graph cancel deferred message network error: %s", exc)
            return False
        return resp.status_code in (200, 204, 404)

    # --- Reply tracking (Phase 6b) ---

    def supports_reply_tracking(self) -> bool:
        return bool(self.tenant_id and self.client_id and self.client_secret and self.send_as_user)

    def _lookup_sent_message(
        self, token: str, subject: str, to_email: str
    ) -> tuple[Optional[str], Optional[str]]:
        """Find the just-sent message in Sent Items to capture conversationId."""
        url = f"{GRAPH_BASE}/users/{self.send_as_user}/mailFolders/sentitems/messages"
        params = {
            "$top": "5",
            "$orderby": "sentDateTime desc",
            "$select": "conversationId,internetMessageId,subject,toRecipients",
        }
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                )
        except httpx.HTTPError as exc:
            logger.info("Graph sent-item lookup skipped (network): %s", exc)
            return None, None
        if resp.status_code != 200:
            # Most likely Mail.Read not granted yet — non-fatal.
            logger.info(
                "Graph sent-item lookup unavailable (%s): %s",
                resp.status_code,
                resp.text[:200],
            )
            return None, None
        for msg in (resp.json() or {}).get("value", []):
            if (msg.get("subject") or "") != subject:
                continue
            recipients = {
                (r.get("emailAddress") or {}).get("address", "").lower()
                for r in msg.get("toRecipients", [])
            }
            if to_email.lower() in recipients:
                return msg.get("conversationId"), msg.get("internetMessageId")
        return None, None

    def check_reply(
        self,
        *,
        to_email: str,
        since: datetime,
        conversation_id: Optional[str] = None,
    ) -> ReplyResult:
        if not self.supports_reply_tracking():
            return ReplyResult(found=False, error="Microsoft Graph not configured.")
        token = self._get_access_token()
        if not token:
            return ReplyResult(found=False, error="Failed to obtain Graph access token.")

        # Graph returns InefficientFilter when $filter combines from/conversationId
        # with receivedDateTime AND $orderby receivedDateTime. Use a single
        # receivedDateTime predicate (orderby-compatible), then match sender /
        # conversation in Python.
        since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"{GRAPH_BASE}/users/{self.send_as_user}/mailFolders/inbox/messages"
        params = {
            "$filter": f"receivedDateTime ge {since_iso}",
            "$top": "50",
            "$orderby": "receivedDateTime desc",
            "$select": (
                "bodyPreview,body,receivedDateTime,from,subject,conversationId"
            ),
        }
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Prefer": 'outlook.body-content-type="text"',
                    },
                    params=params,
                )
        except httpx.HTTPError as exc:
            return ReplyResult(found=False, error=str(exc))

        if resp.status_code == 403:
            return ReplyResult(
                found=False,
                error="Mail.Read permission not granted on the Azure app yet.",
            )
        if resp.status_code != 200:
            return ReplyResult(
                found=False,
                error=f"Graph API {resp.status_code}: {resp.text[:200]}",
            )

        want_from = (to_email or "").strip().lower()
        want_conv = (conversation_id or "").strip()
        our_addr = (self.send_as_user or "").strip().lower()

        for msg in (resp.json() or {}).get("value", []):
            sender = (
                ((msg.get("from") or {}).get("emailAddress") or {}).get("address") or ""
            ).strip().lower()
            if sender and sender == our_addr:
                continue  # ignore our own copies in the inbox

            conv = (msg.get("conversationId") or "").strip()
            if want_conv:
                if conv != want_conv:
                    continue
            elif want_from:
                if sender != want_from:
                    continue
            else:
                continue

            received_raw = msg.get("receivedDateTime")
            received_at = None
            if received_raw:
                try:
                    received_at = datetime.strptime(received_raw[:19], "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    received_at = None
            full_body = ((msg.get("body") or {}).get("content") or "").strip()
            from_name = (
                ((msg.get("from") or {}).get("emailAddress") or {}).get("name") or ""
            ).strip()
            return ReplyResult(
                found=True,
                received_at=received_at,
                snippet=(msg.get("bodyPreview") or "").strip()[:500],
                body=full_body or None,
                from_name=from_name or None,
            )
        return ReplyResult(found=False)

    def _get_access_token(self) -> Optional[str]:
        url = TOKEN_URL.format(tenant=self.tenant_id)
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
                resp = client.post(url, data=data)
        except httpx.HTTPError as exc:
            logger.warning("Microsoft token request network error: %s", exc)
            return None

        if resp.status_code >= 400:
            logger.warning("Microsoft token request failed (%s): %s", resp.status_code, resp.text[:300])
            return None

        return (resp.json() or {}).get("access_token")
