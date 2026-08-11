"""Today's LinkedIn send budget, scoped to one connected account.

LinkedIn applies its invite/message limits PER ACCOUNT, so the daily cap has to
be counted per account too. It used to be counted globally from
``OutreachHistory``, which carries no account column, so every connected account
drew from one shared budget: after 87 sends from other accounts, the account the
user was actually sending from was told its cap of 50 was "reached" while it had
sent nothing that day.

The count comes from :class:`LinkedInMessage` instead, which stamps
``from_account`` at send time (see ``perform_linkedin_send``) and timestamps each
half of the cap separately — ``invitation_sent_at`` for an invitation, ``sent_at``
for a DM. Those two are counted as separate events, exactly as OutreachHistory
recorded them (one row per send action), so only the SCOPE of the cap changes,
never its meaning: a run that invites and later DMs the same person still spends
two units of the day's budget.

The automation path already capped per account this way
(``automation._linkedin_sent_today``); this brings the manual and bulk paths in
line with it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.linkedin_message import LinkedInMessage


def active_send_account_id() -> Optional[str]:
    """The connected account a send started right now would go out from.

    Resolved exactly the way the send itself resolves it — the account selected in
    the UI, else the env default — so the budget is always counted against the
    account that will actually do the sending. Returns ``None`` when it cannot be
    resolved (e.g. the stub provider), which callers treat as "count every
    account" so the cap errs toward being conservative.
    """
    from app.services.linkedin_providers import get_linkedin_provider

    try:
        return getattr(get_linkedin_provider(None), "account_id", None) or None
    except Exception:  # noqa: BLE001 - budgeting must never break the send path
        return None


def linkedin_sent_today(db: Session, account_id: Optional[str]) -> int:
    """Invitations + DMs sent today from ``account_id`` (both spend the cap).

    ``account_id=None`` falls back to counting every account — the previous global
    behaviour — so an unresolvable account can never turn the cap off entirely.

    Rows with no ``from_account`` are counted for whichever account asks. That is
    how the rest of the app attributes those rows (they predate multi-account
    support), and it keeps a single-account install counting exactly what it
    counted before this change.
    """
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    def _count_since(column) -> int:
        query = select(func.count()).select_from(LinkedInMessage).where(column >= start)
        if account_id:
            query = query.where(
                or_(
                    LinkedInMessage.from_account == account_id,
                    LinkedInMessage.from_account.is_(None),
                )
            )
        return int(db.execute(query).scalar_one())

    return _count_since(LinkedInMessage.invitation_sent_at) + _count_since(
        LinkedInMessage.sent_at
    )
