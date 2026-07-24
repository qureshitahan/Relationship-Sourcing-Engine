"""End-to-end test: send outreach from a Gmail / Google Workspace mailbox.

Exercises the exact provider the app uses when EMAIL_PROVIDER=gmail (SMTP send +
optional IMAP reply tracking), delivering to a safe test inbox. No database is
touched, so it runs anywhere the backend deps are installed.

Checks:
  1. Gmail config in .env (GMAIL_ADDRESS / GMAIL_APP_PASSWORD)
  2. Live SMTP send as the configured mailbox
  3. (optional) IMAP reply tracking — poll INBOX for a reply

Usage (from the backend/ directory):
    python scripts/test_gmail_e2e.py                      # sends to yourself
    python scripts/test_gmail_e2e.py someone@example.com  # sends to a recipient
    python scripts/test_gmail_e2e.py you@example.com --watch-reply
        # after sending, reply from that inbox; this polls ~2 min for it

On Windows the interpreter is usually  .venv\\Scripts\\python  (or just python
if the venv is active).
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.services.email_providers import get_email_provider
from app.services.email_providers.gmail import GmailEmailProvider


def line(char: str = "-", width: int = 70) -> None:
    print(char * width)


def _mask(secret: str) -> str:
    if not secret:
        return "(empty)"
    return f"{secret[:2]}...{secret[-2:]} (len {len(secret)})"


def check_config() -> None:
    line("=")
    print("1) Configuration")
    line()
    print(f"  EMAIL_PROVIDER      = {settings.email_provider}")
    print(f"  GMAIL_ADDRESS       = {settings.gmail_address or settings.outreach_from_email}")
    print(f"  GMAIL_APP_PASSWORD  = {_mask(settings.gmail_app_password)}")
    print(f"  OUTREACH_FROM_NAME  = {settings.outreach_from_name}")

    provider = get_email_provider()
    print(f"  provider class      = {type(provider).__name__}")
    print(f"  reply tracking      = {provider.supports_reply_tracking()}")

    missing = [
        name
        for name, val in (
            ("GMAIL_ADDRESS (or OUTREACH_FROM_EMAIL)",
             settings.gmail_address or settings.outreach_from_email),
            ("GMAIL_APP_PASSWORD", settings.gmail_app_password),
        )
        if not val
    ]
    if missing:
        raise SystemExit(f"Missing env: {', '.join(missing)}")
    if settings.email_provider not in ("gmail", "google"):
        print(f"  WARNING: EMAIL_PROVIDER is '{settings.email_provider}', not gmail")


def test_send(recipient: str) -> datetime:
    line("=")
    print("2) Live SMTP send")
    line()
    provider = GmailEmailProvider()
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    subject = f"[RSE E2E] Gmail send test — {stamp}"
    body = (
        "This is an automated end-to-end test from the Relationship Sourcing Engine.\n\n"
        f"Sent as: {provider.address}\n"
        f"Recipient: {recipient}\n"
        f"Time: {stamp}\n\n"
        "If you received this, the Gmail (tekhqs.ai) integration is working.\n"
        "Reply to this email to test reply tracking."
    )
    before = datetime.utcnow()
    result = provider.send(
        to_email=recipient,
        subject=subject,
        body=body,
        from_email=settings.outreach_from_email,
        from_name=settings.outreach_from_name,
    )
    if not result.sent:
        raise SystemExit(f"FAILED send: {result.error}")
    print(f"  sent: OK (provider={result.provider})")
    print(f"  to:   {recipient}")
    print(f"  subj: {subject}")
    print(f"  message-id: {result.message_id}")
    return before


def watch_reply(recipient: str, since: datetime, seconds: int = 120) -> None:
    line("=")
    print(f"3) Watching INBOX for a reply from {recipient} (~{seconds}s)")
    line()
    provider = GmailEmailProvider()
    deadline = time.time() + seconds
    while time.time() < deadline:
        result = provider.check_reply(to_email=recipient, since=since)
        if result.error:
            raise SystemExit(f"FAILED reply check: {result.error}")
        if result.found:
            print("  reply detected!")
            print(f"  from:     {result.from_name}")
            print(f"  received: {result.received_at}")
            print(f"  snippet:  {result.snippet}")
            return
        print("  ...no reply yet, checking again in 10s")
        time.sleep(10)
    print("  no reply seen in the window (send one and re-run with --watch-reply).")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    default_recipient = settings.gmail_address or settings.outreach_from_email
    recipient = (args[0] if args else os.environ.get("TEST_RECIPIENT", default_recipient)).strip()

    print(f"\nGmail E2E test -> {recipient}\n")
    check_config()
    since = test_send(recipient)
    if "--watch-reply" in flags:
        watch_reply(recipient, since)

    line("=")
    print("PASSED — check the recipient inbox (and the mailbox's Sent items).")
    line("=")
    print()


if __name__ == "__main__":
    main()
