"""Send outreach via Gmail / Google Workspace (SMTP) with IMAP reply tracking.

For domains hosted on Google Workspace (e.g. tekhqs.ai) rather than Microsoft
365. This provider is fully independent of the Microsoft Graph provider — it is
only used when EMAIL_PROVIDER=gmail, and leaves Graph behaviour untouched.

Auth is an App Password (no OAuth): it uses only the Python standard library
(smtplib / imaplib), so it adds no new dependencies.

Setup (one-time, per sending mailbox):
  1. Sign in to the mailbox (e.g. dalbir.bains@tekhqs.ai).
  2. Enable 2-Step Verification (myaccount.google.com -> Security).
  3. Create an App Password (Security -> App passwords -> "Mail").
  4. Google Workspace admin: ensure IMAP access and SMTP AUTH are enabled for
     the user (Admin console -> Apps -> Google Workspace -> Gmail).
  5. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in the backend environment, and
     EMAIL_PROVIDER=gmail.

Scheduled sends: Gmail has no server-side deferred delivery here, so
supports_scheduled_send() is False and the app's own in-process scheduler
handles due drafts (identical to the fallback used by every non-Graph provider).

Docs: https://support.google.com/mail/answer/185833 (App passwords)
"""
from __future__ import annotations

import email
import imaplib
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime
from typing import Optional

from app.core.config import settings
from app.services.email_providers.base import EmailProvider, ReplyResult, SendResult

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # STARTTLS
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993  # SSL
TIMEOUT = 30.0


class GmailEmailProvider(EmailProvider):
    name = "gmail"

    def __init__(
        self,
        *,
        address: Optional[str] = None,
        app_password: Optional[str] = None,
    ) -> None:
        # Prefer explicit per-mailbox credentials (multi-account). Fall back to
        # legacy single GMAIL_ADDRESS / GMAIL_APP_PASSWORD for older deploys.
        self.address = (
            (address or "").strip()
            or (settings.gmail_address or settings.outreach_from_email or "").strip()
        )
        self.app_password = (
            (app_password or "").strip() or (settings.gmail_app_password or "").strip()
        )

    def _configured(self) -> bool:
        return bool(self.address and self.app_password)

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
        if not self._configured():
            return SendResult(
                sent=False,
                provider=self.name,
                error=(
                    "Gmail not configured. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
                    "(App Password) in the backend environment."
                ),
            )

        # Gmail sends as the authenticated account; use it as the envelope + From
        # address so the message is not rejected/rewritten. from_name is honoured.
        sender = self.address
        display = f"{from_name} <{sender}>" if from_name else sender

        if html_body:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body or "", "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))
        else:
            msg = MIMEText(body or "", "plain", "utf-8")

        message_id = make_msgid(domain=sender.split("@")[-1] if "@" in sender else None)
        msg["Subject"] = subject
        msg["From"] = display
        msg["To"] = to_email
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = message_id

        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(sender, self.app_password)
                server.sendmail(sender, [to_email], msg.as_string())
        except smtplib.SMTPAuthenticationError as exc:
            logger.warning("Gmail SMTP auth failed: %s", exc)
            return SendResult(
                sent=False,
                provider=self.name,
                error=(
                    "Gmail authentication failed. Check GMAIL_ADDRESS / "
                    "GMAIL_APP_PASSWORD and that SMTP AUTH is enabled for the user."
                ),
            )
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning("Gmail SMTP send error: %s", exc)
            return SendResult(sent=False, provider=self.name, error=str(exc))

        # We store our own Message-ID as the conversation key so replies (which
        # cite it via In-Reply-To / References) can be threaded back to this send.
        return SendResult(
            sent=True,
            provider=self.name,
            message_id=message_id,
            conversation_id=message_id,
            internet_message_id=message_id,
        )

    # --- Reply tracking via IMAP ---

    def supports_reply_tracking(self) -> bool:
        return self._configured()

    def check_reply(
        self,
        *,
        to_email: str,
        since: datetime,
        conversation_id: Optional[str] = None,
    ) -> ReplyResult:
        if not self._configured():
            return ReplyResult(found=False, error="Gmail not configured.")

        # IMAP SEARCH SINCE is date-granular; we filter to the exact timestamp
        # after fetching. Match on sender (the prospect) within the window.
        since_date = since.strftime("%d-%b-%Y")
        try:
            with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=TIMEOUT) as imap:
                imap.login(self.address, self.app_password)
                imap.select("INBOX", readonly=True)
                typ, data = imap.search(
                    None, "FROM", f'"{to_email}"', "SINCE", since_date
                )
                if typ != "OK" or not data or not data[0]:
                    return ReplyResult(found=False)

                # Newest first.
                ids = data[0].split()
                for msg_id in reversed(ids):
                    ftyp, fdata = imap.fetch(msg_id, "(RFC822)")
                    if ftyp != "OK" or not fdata or not fdata[0]:
                        continue
                    raw = fdata[0][1]
                    parsed = email.message_from_bytes(raw)

                    received_at = None
                    if parsed.get("Date"):
                        try:
                            dt = parsedate_to_datetime(parsed["Date"])
                            received_at = (
                                dt.astimezone(tz=None).replace(tzinfo=None)
                                if dt.tzinfo
                                else dt
                            )
                        except (TypeError, ValueError):
                            received_at = None
                    if received_at is not None and received_at < since:
                        continue

                    body_text = _extract_text(parsed).strip()
                    from_name, _addr = parseaddr(parsed.get("From", ""))
                    snippet = " ".join(body_text.split())[:500]
                    return ReplyResult(
                        found=True,
                        received_at=received_at,
                        snippet=snippet or None,
                        body=body_text or None,
                        from_name=(from_name or None),
                    )
        except imaplib.IMAP4.error as exc:
            logger.info("Gmail IMAP reply check unavailable: %s", exc)
            return ReplyResult(
                found=False,
                error=(
                    "Gmail IMAP access failed. Ensure IMAP is enabled for the "
                    "mailbox and the App Password is correct."
                ),
            )
        except OSError as exc:
            return ReplyResult(found=False, error=str(exc))

        return ReplyResult(found=False)


def _extract_text(message: email.message.Message) -> str:
    """Return the best-effort plain-text body of an email message."""
    if message.is_multipart():
        # Prefer a text/plain part; skip attachments.
        for part in message.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition", "")
            ):
                payload = part.get_payload(decode=True)
                if payload is not None:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return ""
    payload = message.get_payload(decode=True)
    if payload is None:
        return ""
    charset = message.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")
