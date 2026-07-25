"""Smoke test the bulk lookup pipeline against a pasted roster.

Runs in a throwaway database so it never touches dev data:

    DATABASE_URL=sqlite:///./smoke_lookup.db python scripts/smoke_bulk_lookup.py roster.txt

Stage one (parsing people out of the paste) always runs. Stage two (identifying
them on the web and asking Apollo for addresses) costs money, so it only runs
with --resolve, and --limit caps how many people it tries.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.models.bulk_campaign import BulkCampaign, BulkLookup  # noqa: E402
from app.models.contact import Contact  # noqa: E402
from app.models.enums import BulkCampaignStatus  # noqa: E402
from app.services.bulk_email.chat import handle_message  # noqa: E402
from app.services.bulk_email.resolver import (  # noqa: E402
    PersonQuery,
    find_emails,
    identify,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "paste",
        type=Path,
        nargs="?",
        help="File holding the pasted list (omit when using --say)",
    )
    parser.add_argument(
        "--say",
        action="append",
        default=[],
        metavar="MESSAGE",
        help="Send a conversational turn instead of a paste; repeatable",
    )
    parser.add_argument("--resolve", action="store_true", help="Run the paid lookup stage")
    parser.add_argument("--limit", type=int, default=3, help="People to resolve")
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated name fragments; resolve only these people",
    )
    args = parser.parse_args()
    wanted = [part.strip().lower() for part in args.only.split(",") if part.strip()]

    init_db()
    db = SessionLocal()
    try:
        campaign = BulkCampaign(
            name="Smoke: pasted roster",
            mailbox_id=None,
            status=BulkCampaignStatus.COLLECTING,
        )
        db.add(campaign)
        db.commit()

        turns = list(args.say) or [args.paste.read_text()]
        for turn in turns:
            result = handle_message(db, campaign, turn)
            print(f"\n=== you: {turn[:90]}")
            print("--- assistant ---")
            print(result.reply)
            print(
                f"[with address +{result.recipients_added} | "
                f"needing lookup +{result.needs_lookup} | "
                f"brief {'set' if campaign.purpose else 'empty'}]"
            )
        print(f"\nbrief: {campaign.purpose}")

        lookups = db.query(BulkLookup).filter_by(campaign_id=campaign.id).all()
        print(f"\n--- queued for lookup ({len(lookups)}) ---")
        for lookup in lookups:
            contact = db.get(Contact, lookup.contact_id)
            company = contact.company.name if contact.company else None
            flag = " [approx spelling]" if lookup.reason else ""
            print(f"  {contact.name}{flag} — {contact.title} @ {company}")
            print(f"      from : {(lookup.source_text or '')[:120]}")

        if not args.resolve:
            print("\nSkipped the web/Apollo stage. Re-run with --resolve to try it.")
            return 0

        if wanted:
            sample = [
                lookup
                for lookup in lookups
                if any(
                    fragment in (db.get(Contact, lookup.contact_id).name or "").lower()
                    for fragment in wanted
                )
            ]
        else:
            sample = lookups[: args.limit]
        print(f"\n--- resolving {len(sample)} of them ---")
        pairs = []
        for lookup in sample:
            contact = db.get(Contact, lookup.contact_id)
            query = PersonQuery(
                lookup_id=lookup.id,
                name=contact.name,
                title=contact.title,
                company=contact.company.name if contact.company else None,
                source_text=lookup.source_text,
            )
            identity = identify(query)
            pairs.append((query, identity))
            print(f"\n  {contact.name}")
            print(f"      found      : {identity.found} (conf {identity.confidence:.2f})")
            print(f"      ambiguous  : {identity.ambiguous}")
            print(f"      employer   : {identity.organization} / {identity.domain}")
            print(f"      linkedin   : {identity.linkedin_url}")
            print(f"      reason     : {identity.reason}")
            print(f"      sources    : {[s['url'] for s in identity.sources][:3]}")

        matches = find_emails(pairs)
        print(f"\n--- Apollo returned {len(matches)} match(es) ---")
        for query, _identity in pairs:
            contact = matches.get(query.lookup_id)
            if contact is None:
                print(f"  {query.name}: not sent to Apollo (no usable identifier)")
            else:
                print(f"  {query.name}: {contact.email} ({contact.email_status})")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
