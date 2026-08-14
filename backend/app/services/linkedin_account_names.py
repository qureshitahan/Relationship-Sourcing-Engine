"""Display names for LinkedIn sending accounts, stored locally.

The provider (Unipile ``/accounts``) is the only place account names exist, and
it is a live network call that returns an empty list on any failure. That is fine
for a picker the user is actively driving, but not for a report: a team reading
per-account performance needs to know whose account a row belongs to, and an
outage must not turn that report into opaque provider ids.

So names are cached here, in the existing ``app_settings`` key/value store, as a
single JSON blob keyed by account id::

    {"<account_id>": {"name": "Taha", "manual": true}}

``manual`` records where the name came from. A name the user typed always wins:
a later provider sync refreshes only the auto-filled entries, so a chosen label
is never silently overwritten by whatever Unipile happens to call the account.

This module deliberately owns no schema of its own. Four-ish accounts of display
metadata do not justify a table, and reusing ``app_settings`` means nothing has
to be migrated for names to start working.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from app.services.app_settings import get_setting, set_setting

logger = logging.getLogger(__name__)

NAMES_KEY = "linkedin_account_names"

#: Accounts whose by-id lookup came back empty, and when. See
#: ``should_attempt_lookup``.
FAILED_LOOKUPS_KEY = "linkedin_account_name_lookups_failed"

#: Guard against an unbounded blob if a provider ever returns junk ids.
MAX_NAME_LEN = 120

#: How long to leave a failed by-id lookup alone. An account removed from the
#: provider will never resolve, and retrying it on every page load would spend a
#: request per view forever. A day is short enough that a reconnected account is
#: picked up on its own, and long enough that the wasted calls stop.
LOOKUP_RETRY_HOURS = 24


def _load() -> dict[str, dict[str, Any]]:
    """Read the blob, treating any damage as "no names known".

    Names are decoration. A malformed blob must degrade to provider ids, never
    raise into a page that would otherwise render.
    """
    raw = get_setting(NAMES_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("linkedin_account_names is not valid JSON; ignoring it")
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for account_id, entry in data.items():
        if not isinstance(account_id, str) or not account_id.strip():
            continue
        # Tolerate a bare string from any hand-edit: treat it as a manual label.
        if isinstance(entry, str):
            out[account_id] = {"name": _tidy(entry), "manual": True}
        elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
            out[account_id] = {
                "name": _tidy(entry["name"]),
                "manual": bool(entry.get("manual")),
            }
    return out


def _tidy(name: str) -> str:
    """Trim and collapse whitespace, and cap the length.

    Applied on read as well as on write, so names already cached with the
    provider's doubled spaces ("Filza  Noor") display cleanly without waiting for
    the next successful sync to rewrite them.
    """
    return " ".join(name.split())[:MAX_NAME_LEN]


def _save(data: dict[str, dict[str, Any]]) -> None:
    set_setting(NAMES_KEY, json.dumps(data))


def entries() -> dict[str, dict[str, Any]]:
    """Every known account name with its origin, for a UI that offers editing."""
    return _load()


def resolved_names() -> dict[str, str]:
    """``{account_id: display name}`` — the shape a report needs.

    Read straight from the database, so a dashboard can label its rows without
    depending on the provider being reachable.
    """
    return {
        account_id: entry["name"]
        for account_id, entry in _load().items()
        if entry.get("name")
    }


def remember_provider_names(accounts: list[dict[str, Any]] | None) -> None:
    """Cache names from a successful provider listing.

    Called on the account-list path, so simply opening the LinkedIn page keeps
    the cache warm. Entries the user set by hand are left alone, and an empty
    listing (which is also what a failed call returns) changes nothing — a
    provider outage must never erase names that were already learned.
    """
    if not accounts:
        return
    data = _load()
    changed = False
    for account in accounts:
        if not isinstance(account, dict):
            continue
        account_id = str(account.get("id") or "").strip()
        # The provider returns names like "Filza  Noor" with doubled spaces, which
        # read as typos in a table, so tidy before storing.
        name = _tidy(str(account.get("name") or account.get("username") or ""))
        if not account_id or not name:
            continue
        existing = data.get(account_id)
        if existing and existing.get("manual"):
            continue  # a typed label outranks the provider's
        if existing and existing.get("name") == name:
            continue
        data[account_id] = {"name": name, "manual": False}
        # It answered, so any earlier "could not resolve" note is stale.
        clear_lookup_failure(account_id)
        changed = True
    if changed:
        try:
            _save(data)
        except Exception:  # noqa: BLE001 - caching a label must not fail a request
            logger.warning("could not persist LinkedIn account names", exc_info=True)


def _load_failed() -> dict[str, str]:
    raw = get_setting(FAILED_LOOKUPS_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return (
        {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
        if isinstance(data, dict)
        else {}
    )


def should_attempt_lookup(account_id: str) -> bool:
    """Whether a by-id name lookup for this account is worth making now.

    An account that has been removed from the provider answers "not found" every
    single time. Without this, one dead account costs an outbound request on every
    dashboard load, forever — so a failed lookup is remembered and skipped until
    ``LOOKUP_RETRY_HOURS`` has passed.
    """
    stamp = _load_failed().get((account_id or "").strip())
    if not stamp:
        return True
    try:
        last = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    return datetime.utcnow() - last >= timedelta(hours=LOOKUP_RETRY_HOURS)


def mark_lookup_failed(account_id: str) -> None:
    """Record that this account could not be named, so it is not retried at once."""
    account_id = (account_id or "").strip()
    if not account_id:
        return
    data = _load_failed()
    data[account_id] = datetime.utcnow().isoformat()
    try:
        set_setting(FAILED_LOOKUPS_KEY, json.dumps(data))
    except Exception:  # noqa: BLE001 - bookkeeping must not fail a request
        logger.warning("could not persist failed name lookups", exc_info=True)


def clear_lookup_failure(account_id: str) -> None:
    """Forget a past failure, so a reconnected account is looked up again."""
    account_id = (account_id or "").strip()
    data = _load_failed()
    if account_id in data:
        data.pop(account_id, None)
        try:
            set_setting(FAILED_LOOKUPS_KEY, json.dumps(data))
        except Exception:  # noqa: BLE001
            logger.warning("could not clear failed name lookup", exc_info=True)


def set_manual_name(account_id: str, name: str | None) -> dict[str, dict[str, Any]]:
    """Set or clear a hand-typed label for one account.

    A blank name removes the entry entirely rather than storing an empty string,
    which lets the next provider sync re-fill it automatically — "reset to
    whatever LinkedIn calls it" with no separate control.
    """
    account_id = (account_id or "").strip()
    if not account_id:
        raise ValueError("account_id is required")
    data = _load()
    cleaned = (name or "").strip()[:MAX_NAME_LEN]
    if cleaned:
        data[account_id] = {"name": cleaned, "manual": True}
    else:
        data.pop(account_id, None)
    _save(data)
    return data
