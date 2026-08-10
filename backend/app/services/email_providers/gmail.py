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
import time
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

# A fresh connect+TLS+login for every single message is most of what made a
# 500-email bulk send slow (each one is a full network round trip before any
# actual mail data moves) — so one connection is held open and reused across
# calls to send() instead. Recycled after this many messages anyway, as cheap
# insurance against any undocumented per-connection cap Gmail may apply.
_MAX_SENDS_PER_CONNECTION = 80
# Transient failures (dropped connection, "come back later" 4xx codes) get a
# couple of retries with a short backoff before giving up — a hard rejection
# (bad auth, daily cap, invalid recipient) never gets retried, since trying
# the exact same send again cannot change the outcome.
_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 3.0

# Substrings Gmail includes in its raw SMTP rejection text, mapped to a
# human-readable message. Raw SMTP errors look like `(550, b'5.4.5 Daily
# user sending limit exceeded...')` — useless to an operator staring at a
# bulk-send failure banner, so known cases get translated.
_FRIENDLY_SMTP_MESSAGES = (
    (
        "daily user sending limit",
        "Gmail's daily sending limit for this account was reached. Try again "
        "after it resets (~24h), or send from a different mailbox.",
    ),
)


def _clean_smtp_error(code: Optional[int], raw_message: object) -> str:
    text = raw_message.decode("utf-8", "replace") if isinstance(raw_message, bytes) else str(raw_message)
    lowered = text.lower()
    for needle, friendly in _FRIENDLY_SMTP_MESSAGES:
        if needle in lowered:
            return friendly
    return f"Gmail rejected the send ({code}): {text.strip()}"


class GmailEmailProvider(EmailProvider):
    name = "gmail"

    def __init__(
        self,
        address: Optional[str] = None,
        app_password: Optional[str] = None,
    ) -> None:
        # Per-mailbox creds when provided (multi-sender); otherwise fall back to
        # the global GMAIL_* / OUTREACH_FROM_EMAIL settings (single-sender).
        self.address = (
            address or settings.gmail_address or settings.outreach_from_email or ""
        ).strip()
        self.app_password = (app_password or settings.gmail_app_password or "").strip()
        # Persistent SMTP session, reused across send() calls within a batch
        # (see _ensure_connection). None when not currently connected.
        self._connection: Optional[smtplib.SMTP] = None
        self._sends_on_connection = 0

    def _configured(self) -> bool:
        return bool(self.address and self.app_password)

    def _ensure_connection(self) -> smtplib.SMTP:
        """Return an authenticated SMTP session, reusing one already open on
        this provider instance. Recycled after _MAX_SENDS_PER_CONNECTION
        messages as a precaution, not because Gmail documents a hard cap."""
        if self._connection is not None and self._sends_on_connection >= _MAX_SENDS_PER_CONNECTION:
            self._close_connection()
        if self._connection is None:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(self.address, self.app_password)
            self._connection = server
            self._sends_on_connection = 0
        return self._connection

    def _close_connection(self) -> None:
        if self._connection is not None:
            try:
                self._connection.quit()
            except Exception:  # noqa: BLE001 - best-effort close, connection may already be dead
                pass
            self._connection = None
            self._sends_on_connection = 0

    def close(self) -> None:
        """Release the persistent SMTP session — call once a bulk-send batch
        finishes (or fails) so the connection isn't left open indefinitely."""
        self._close_connection()

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

        for attempt in range(_MAX_RETRIES + 1):
            last_attempt = attempt == _MAX_RETRIES
            try:
                server = self._ensure_connection()
                server.sendmail(sender, [to_email], msg.as_string())
                self._sends_on_connection += 1
                break
            except smtplib.SMTPAuthenticationError as exc:
                # Bad creds — retrying changes nothing, and the dead connection
                # (if any) is not worth keeping around.
                logger.warning("Gmail SMTP auth failed: %s", exc)
                self._close_connection()
                return SendResult(
                    sent=False,
                    provider=self.name,
                    error=(
                        "Gmail authentication failed. Check GMAIL_ADDRESS / "
                        "GMAIL_APP_PASSWORD and that SMTP AUTH is enabled for the user."
                    ),
                )
            except smtplib.SMTPRecipientsRefused as exc:
                code, raw = next(iter(exc.recipients.values()), (None, ""))
                # Codes below 500 are SMTP's own "temporary failure, try again"
                # family (e.g. greylisting) — worth a retry. 5xx is permanent.
                if code and code < 500 and not last_attempt:
                    logger.warning(
                        "Gmail transient recipient-refused (attempt %s/%s), retrying: %s",
                        attempt + 1, _MAX_RETRIES + 1, raw,
                    )
                    time.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                logger.warning("Gmail SMTP recipients refused: %s", exc.recipients)
                return SendResult(sent=False, provider=self.name, error=_clean_smtp_error(code, raw))
            except smtplib.SMTPResponseException as exc:
                # Covers SMTPConnectError/SMTPDataError/SMTPSenderRefused/SMTPHeloError
                # (SMTPAuthenticationError, also in this family, is caught above).
                if exc.smtp_code and exc.smtp_code < 500 and not last_attempt:
                    logger.warning(
                        "Gmail transient error %s (attempt %s/%s), retrying",
                        exc.smtp_code, attempt + 1, _MAX_RETRIES + 1,
                    )
                    time.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                logger.warning("Gmail SMTP error %s: %s", exc.smtp_code, exc.smtp_error)
                return SendResult(
                    sent=False,
                    provider=self.name,
                    error=_clean_smtp_error(exc.smtp_code, exc.smtp_error),
                )
            except OSError as exc:
                # Everything else lands here — smtplib.SMTPException (incl.
                # SMTPServerDisconnected) is itself an OSError subclass in
                # Python 3, and this also catches raw socket-level failures
                # (connection refused, timeout). None of these are a rejection
                # of this specific message, so the connection is discarded and
                # retried on a fresh one — this MUST stay the last except
                # clause: OSError is a parent of every smtplib exception above,
                # so anything more specific has to be caught before this.
                self._close_connection()
                if not last_attempt:
                    logger.warning(
                        "Gmail SMTP connection lost (attempt %s/%s), reconnecting: %s",
                        attempt + 1, _MAX_RETRIES + 1, exc,
                    )
                    time.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                logger.warning("Gmail SMTP connection lost after %s attempts: %s", attempt + 1, exc)
                return SendResult(
                    sent=False,
                    provider=self.name,
                    error=f"Gmail connection error after {attempt + 1} attempts: {exc}",
                )

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
