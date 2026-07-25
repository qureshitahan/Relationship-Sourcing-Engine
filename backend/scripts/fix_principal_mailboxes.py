"""Assign each principal a send-from mailbox and repair unsent drafts.

Agent campaigns used to leave EmailDraft.from_mailbox empty, so every send fell
back to the global default sender (OUTREACH_FROM_EMAIL) regardless of which
principal the campaign belonged to. This script:

  1. Reports each principal and the mailbox they are (or aren't) bound to.
  2. Optionally sets a principal's mailbox, matching on email/name.
  3. Stamps from_mailbox on their unsent drafts so the UI shows the true sender.

Usage:
    # See the current state and what is mis-addressed.
    cd backend && .venv/bin/python scripts/fix_principal_mailboxes.py

    # Bind a principal to a mailbox (repeatable), then fix their unsent drafts.
    cd backend && .venv/bin/python scripts/fix_principal_mailboxes.py \
        --assign 3=taha_tekhqs --assign 1=dalbir_tekhqs --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.session import SessionLocal, init_db
from app.models.email_draft import EmailDraft
from app.models.enums import EmailStatus
from app.models.principal import Principal
from app.services.email_providers import list_mailboxes

UNSENT = [EmailStatus.DRAFT, EmailStatus.APPROVED, EmailStatus.SCHEDULED]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--assign",
        action="append",
        default=[],
        metavar="PRINCIPAL_ID=MAILBOX_ID",
        help="Bind a principal to a mailbox, e.g. --assign 3=taha_tekhqs",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write changes (default is a preview)."
    )
    args = parser.parse_args()

    init_db()
    boxes = {mb.id: mb for mb in list_mailboxes()}

    print("Configured mailboxes:")
    for mb in boxes.values():
        print(f"  {mb.id:<16} {mb.from_email:<36} ({mb.provider})")
    print()

    assignments: dict[int, str] = {}
    for raw in args.assign:
        if "=" not in raw:
            print(f"ERROR: --assign expects PRINCIPAL_ID=MAILBOX_ID, got {raw!r}")
            return 2
        pid_raw, mb_id = raw.split("=", 1)
        mb_id = mb_id.strip()
        if mb_id not in boxes:
            print(f"ERROR: unknown mailbox {mb_id!r}. Known: {', '.join(boxes)}")
            return 2
        assignments[int(pid_raw)] = mb_id

    db = SessionLocal()
    try:
        principals = db.execute(select(Principal)).scalars().all()
        changed_drafts = 0

        for p in principals:
            if p.id in assignments:
                p.outreach_mailbox_id = assignments[p.id]

            current = p.outreach_mailbox_id
            box = boxes.get(current or "")
            label = box.from_email if box else "** NOT SET **"
            print(f"Principal {p.id}: {p.name}")
            print(f"  sends from: {label}")

            drafts = db.execute(
                select(EmailDraft).where(
                    EmailDraft.principal_id == p.id,
                    EmailDraft.status.in_(UNSENT),
                )
            ).scalars().all()
            # Only unsent mail is safe to re-stamp; sent history stays as-is.
            mismatched = [d for d in drafts if (d.from_mailbox or None) != current]
            print(f"  unsent drafts: {len(drafts)} ({len(mismatched)} to re-stamp)")

            if box and mismatched:
                for d in mismatched:
                    if d.outlook_scheduled:
                        # Already queued at Exchange from the old sender; unschedule
                        # it in the app before it can be re-addressed.
                        print(
                            f"    draft {d.id}: queued in Outlook — unschedule it first"
                        )
                        continue
                    d.from_mailbox = current
                    changed_drafts += 1
            print()

        if not args.apply:
            db.rollback()
            print("Preview only — re-run with --apply to write these changes.")
            return 0

        db.commit()
        print(f"Applied. Re-stamped {changed_drafts} unsent draft(s).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
