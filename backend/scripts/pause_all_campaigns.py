"""Emergency stop: pause every campaign and pull back pending sends.

Turns off daily autopilot on all campaigns and reverts anything still queued
(SCHEDULED) back to APPROVED so it will not go out. Outlook server-side
scheduled sends are cancelled at the provider too, since those would otherwise
be delivered by Exchange regardless of this app.

    cd backend && .venv/bin/python scripts/pause_all_campaigns.py
    # preview only, change nothing:
    cd backend && .venv/bin/python scripts/pause_all_campaigns.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.session import SessionLocal, init_db
from app.models.agent_config import AgentConfig
from app.models.email_draft import EmailDraft
from app.models.enums import EmailStatus


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would change, change nothing."
    )
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        configs = db.execute(select(AgentConfig)).scalars().all()
        to_disable = [c for c in configs if c.enabled]
        scheduled = db.execute(
            select(EmailDraft).where(EmailDraft.status == EmailStatus.SCHEDULED)
        ).scalars().all()

        print(f"Campaigns: {len(configs)} total, {len(to_disable)} with autopilot on")
        print(f"Scheduled emails still queued: {len(scheduled)}")
        outlook = [d for d in scheduled if d.outlook_scheduled]
        if outlook:
            print(f"  of which handed to Outlook (need provider cancel): {len(outlook)}")

        if args.dry_run:
            print("\nDry run — nothing changed.")
            return 0

        for config in to_disable:
            config.enabled = False
        print(f"\nAutopilot turned off on {len(to_disable)} campaign(s).")

        cancelled = 0
        failed: list[int] = []
        for draft in scheduled:
            if draft.outlook_scheduled and draft.provider_message_id:
                # Delete the queued copy at Exchange, or it sends anyway.
                from app.services.email_providers import (
                    provider_for_mailbox,
                    resolve_mailbox,
                )

                provider = provider_for_mailbox(resolve_mailbox(draft.from_mailbox))
                try:
                    ok = provider.cancel_scheduled(
                        remote_message_id=draft.provider_message_id
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  draft {draft.id}: cancel error {exc}")
                    ok = False
                if not ok:
                    failed.append(draft.id)
                    continue
                draft.provider_message_id = None
                draft.conversation_id = None
                draft.internet_message_id = None
            draft.status = EmailStatus.APPROVED
            draft.scheduled_at = None
            draft.outlook_scheduled = False
            cancelled += 1

        db.commit()
        print(f"Pulled {cancelled} scheduled email(s) back to APPROVED (not sending).")
        if failed:
            print(
                f"WARNING: could not cancel {len(failed)} Outlook-queued email(s): "
                f"{failed}. Check the mailbox Outbox/Sent Items manually."
            )
        print("\nNothing will send until you re-enable a campaign.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
