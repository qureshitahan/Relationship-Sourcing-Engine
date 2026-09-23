"""Which connected LinkedIn accounts belong to the SAME person.

A Unipile ``account_id`` identifies a CONNECTION, not a human. Re-linking an
account can mint a brand-new id for the same LinkedIn profile, and every dedup
scope in this app is keyed on that id — ``linkedin_follower_sends``,
``linkedin_search_sends``, ``LinkedInMessage.from_account``. So a new id makes
the entire "who have we already messaged" history stop applying, and the next run
messages everyone a second time.

That is not hypothetical. On 2026-09-02 Dalbir Bains was re-linked; Unipile
created ``CCJWKPI5SLSX8K-ZVwCz5A`` beside the existing ``SxtgBQjdSGmb8a5TQqFG7g``,
the 7,408 checkpoint rows under the old id became invisible, and on 2026-09-23 a
connection messaged in August received the identical DM again.

Two defences, and both are needed:

* ``create_hosted_auth_link(reconnect_account_id=...)`` asks Unipile to REVIVE
  the connection instead of adding one, so no new id appears. That removes the
  cause but cannot help where a new id already exists, or where Unipile declines
  to reconnect and mints one anyway.
* This module is the safety net. It remembers the LinkedIn **member id** of
  whoever each connection logs in as — that belongs to the person and never
  changes — so two ids with the same owner are recognisably one account, and a
  successor inherits its predecessor's history.

Storage mirrors ``linkedin_account_names``: one JSON blob in the existing
``app_settings`` store, no schema of its own::

    {"<account_id>": {"owner": "<member id>", "username": "...",
                      "linked_to": "<account_id>", "manual": true}}

Entries are **never removed**. An account that drops off the provider listing is
exactly the one whose history a successor needs to inherit, so forgetting it
would defeat the purpose.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from app.services.app_settings import get_setting, set_setting

logger = logging.getLogger(__name__)

IDENTITY_KEY = "linkedin_account_identities"

#: Guard against an unbounded blob if a provider ever returns junk.
MAX_ID_LEN = 255


def _load() -> dict[str, dict[str, Any]]:
    """Read the blob, treating any damage as "nothing known".

    A malformed blob must degrade to "this account has no known siblings" — which
    is the pre-existing behaviour — never raise into a send that would otherwise
    run correctly.
    """
    raw = get_setting(IDENTITY_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Damaged %s blob; treating as empty", IDENTITY_KEY)
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(k): v for k, v in data.items() if isinstance(v, dict) and str(k).strip()
    }


def _save(data: dict[str, dict[str, Any]]) -> None:
    try:
        set_setting(IDENTITY_KEY, json.dumps(data))
    except Exception:  # noqa: BLE001 - bookkeeping must never fail a send
        logger.exception("Could not persist %s", IDENTITY_KEY)


def _clean(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text or len(text) > MAX_ID_LEN:
        return None
    return text


def remember_account_owners(accounts: list[dict[str, Any]] | None) -> None:
    """Record who each listed connection belongs to.

    Called on the account-listing path, so simply opening a LinkedIn page keeps
    the register warm — and critically, records an account's owner BEFORE it is
    ever re-linked, which is the only moment that information is available.

    An empty listing (also what a failed provider call returns) changes nothing.
    """
    if not accounts:
        return
    data = _load()
    changed = False
    for account in accounts:
        if not isinstance(account, dict):
            continue
        account_id = _clean(account.get("id"))
        if not account_id:
            continue
        owner = _clean(account.get("owner_member_id"))
        username = _clean(account.get("owner_username"))
        if not owner and not username:
            continue
        existing = data.get(account_id) or {}
        # A hand-made link outranks the provider: it is the only way to repair a
        # predecessor that has already dropped off the listing.
        if existing.get("manual"):
            continue
        if existing.get("owner") == owner and existing.get("username") == username:
            continue
        entry = dict(existing)
        entry.update(
            {
                "owner": owner,
                "username": username,
                "seen_at": datetime.utcnow().isoformat(),
            }
        )
        data[account_id] = entry
        changed = True
    if changed:
        _save(data)


def link_accounts(account_id: str, same_person_as: str) -> bool:
    """Declare by hand that two ids are the same person. True when recorded.

    Needed because automatic matching only works while BOTH ids are still on the
    provider listing. A predecessor that Unipile has already replaced is gone
    from it, so the link it would have supplied can only be stated.
    """
    new_id = _clean(account_id)
    old_id = _clean(same_person_as)
    if not new_id or not old_id or new_id == old_id:
        return False
    data = _load()
    entry = dict(data.get(new_id) or {})
    entry["linked_to"] = old_id
    entry["manual"] = True
    entry["seen_at"] = datetime.utcnow().isoformat()
    data[new_id] = entry
    _save(data)
    return True


def sibling_account_ids(account_id: Optional[str]) -> list[str]:
    """Every account id belonging to the same person as ``account_id``.

    ALWAYS includes ``account_id`` itself, and returns exactly ``[account_id]``
    when nothing is known — so a caller that swaps ``== account_id`` for
    ``.in_(sibling_account_ids(account_id))`` behaves identically until a genuine
    alias exists. That is what makes wiring this into the dedup path safe.

    Matching is transitive and walks both directions: a hand-made ``linked_to``
    chain, plus anyone sharing the same LinkedIn member id (or, failing that, the
    same public username).
    """
    start = _clean(account_id)
    if not start:
        return []
    data = _load()
    if not data:
        return [start]

    # Build an undirected adjacency of "same person" edges once, then walk it, so
    # a three-way chain (an account re-linked twice) resolves as one group rather
    # than only its nearest neighbour.
    edges: dict[str, set[str]] = {}

    def connect(a: str, b: str) -> None:
        if a == b:
            return
        edges.setdefault(a, set()).add(b)
        edges.setdefault(b, set()).add(a)

    by_owner: dict[str, list[str]] = {}
    by_username: dict[str, list[str]] = {}
    for acct, entry in data.items():
        linked = _clean(entry.get("linked_to"))
        if linked:
            connect(acct, linked)
        owner = _clean(entry.get("owner"))
        if owner:
            by_owner.setdefault(owner, []).append(acct)
        else:
            # Username is the weaker key, used only when no member id is known —
            # matching on it as well as a member id could merge two people whose
            # ids already disagree.
            username = _clean(entry.get("username"))
            if username:
                by_username.setdefault(username, []).append(acct)
    for group in list(by_owner.values()) + list(by_username.values()):
        for other in group[1:]:
            connect(group[0], other)

    seen = {start}
    queue = [start]
    while queue:
        current = queue.pop()
        for neighbour in edges.get(current, ()):  # noqa: B007
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    # Stable order, with the asked-for account first: it is the one a caller
    # writes new rows under.
    return [start] + sorted(seen - {start})


def entries() -> dict[str, dict[str, Any]]:
    """The whole register, for display and troubleshooting."""
    return _load()
