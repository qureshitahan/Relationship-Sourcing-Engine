"""Re-point research that was saved against the wrong principal.

Why this exists: until the fix in routes/prospects.py, the Research button sent
no principal, so the backend fell back to "oldest principal by id" — which on a
multi-principal deployment is almost never the right one, and after a soft
delete can even be a deactivated principal. The result: the Prospects page shows
a prospect as researched (relevance_score is set on the contact, shared across
principals) while drafting rejects it, because RelevanceInsight is per-principal
and none exists for the campaign's principal.

This moves each misattributed insight onto the principal the prospect actually
belongs to, derived the same way the fixed endpoint derives it: campaign first,
then discovery run. Nothing is re-researched, so no Anthropic tokens are spent.

    python -m scripts.fix_insight_principal            # dry run, prints a plan
    python -m scripts.fix_insight_principal --apply    # make the changes

Safe to re-run: a second pass finds nothing left to move.
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.agent_config import AgentConfig  # noqa: E402
from app.models.contact import Contact  # noqa: E402
from app.models.discovery_run import DiscoveryRun  # noqa: E402
from app.models.principal import Principal  # noqa: E402
from app.models.relevance_insight import RelevanceInsight  # noqa: E402


def owning_principal_id(db, contact: Contact) -> int | None:
    """The principal this prospect belongs to: campaign first, then its run."""
    if contact.campaign_id:
        config = db.get(AgentConfig, contact.campaign_id)
        if config and config.principal_id:
            return config.principal_id
    if contact.discovery_run_id:
        run = db.get(DiscoveryRun, contact.discovery_run_id)
        if run and run.principal_id:
            return run.principal_id
    return None


def main(apply: bool) -> int:
    db = SessionLocal()
    try:
        names = {p.id: p.name for p in db.execute(select(Principal)).scalars().all()}
        insights = db.execute(select(RelevanceInsight)).scalars().all()
        print(f"scanning {len(insights)} insight(s)...\n")

        moves: list[tuple[RelevanceInsight, int, int]] = []
        conflicts = 0
        orphans = 0

        for ins in insights:
            if ins.contact_id is None:
                continue
            contact = db.get(Contact, ins.contact_id)
            if contact is None:
                continue
            owner = owning_principal_id(db, contact)
            if owner is None:
                orphans += 1
                continue
            if owner == ins.principal_id:
                continue
            # UNIQUE(principal_id, contact_id): if the right principal already
            # has its own insight for this prospect, moving would collide — the
            # correct research already exists, so leave the stale row alone.
            existing = db.execute(
                select(RelevanceInsight).where(
                    RelevanceInsight.principal_id == owner,
                    RelevanceInsight.contact_id == ins.contact_id,
                )
            ).scalars().first()
            if existing is not None:
                conflicts += 1
                continue
            moves.append((ins, ins.principal_id, owner))

        if not moves:
            print("nothing to move — every insight already sits on the right principal.")
        else:
            summary = Counter(
                (f"{names.get(old, old)} (#{old})", f"{names.get(new, new)} (#{new})")
                for _, old, new in moves
            )
            print("planned moves:")
            for (old, new), count in summary.most_common():
                print(f"  {count:>5} insight(s):  {old}  ->  {new}")

        print(
            f"\ntotal to move: {len(moves)}"
            f" | skipped (correct research already exists): {conflicts}"
            f" | no owning principal: {orphans}"
        )

        if not apply:
            print("\nDRY RUN — nothing written. Re-run with --apply to make these changes.")
            return 0

        for ins, _old, new in moves:
            ins.principal_id = new
        db.commit()
        print(f"\nAPPLIED: moved {len(moves)} insight(s).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))
