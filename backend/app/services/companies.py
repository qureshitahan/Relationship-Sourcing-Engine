"""Company helpers: get-or-create with normalized-name dedup."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.company import Company
from app.services.normalization import normalize_company_name


def get_or_create_company(
    db: Session,
    name: Optional[str],
    linkedin_url: Optional[str] = None,
) -> Optional[Company]:
    """Find a company by normalized name or create it. Returns None if no name."""
    if not name:
        return None
    normalized = normalize_company_name(name)
    if not normalized:
        return None

    # ``first()``, never ``scalar_one_or_none()``: duplicate normalized_name rows
    # exist in databases created before the unique index below, and a duplicate
    # must not raise MultipleResultsFound. That exception aborted the whole
    # discovery run the moment it touched an affected company, so "Run now"
    # appeared to do nothing. Oldest row wins, matching the merge migration.
    company = db.execute(
        select(Company)
        .where(Company.normalized_name == normalized)
        .order_by(Company.id.asc())
    ).scalars().first()

    if company is None:
        pending = Company(name=name.strip(), normalized_name=normalized)
        try:
            # SAVEPOINT, not a plain flush: a losing race must undo only this
            # insert. A bare rollback here would discard the caller's whole
            # in-progress import (the contact being created around this call).
            with db.begin_nested():
                db.add(pending)
                db.flush()  # assign an id without committing the outer transaction
            company = pending
        except IntegrityError:
            # Another concurrent run inserted the same company between our read
            # and this flush. The unique index is what turns that race into a
            # clean error instead of a duplicate row, so honour it and adopt the
            # row the other run wrote.
            if pending in db:  # savepoint rollback usually expunges it already
                db.expunge(pending)
            company = db.execute(
                select(Company)
                .where(Company.normalized_name == normalized)
                .order_by(Company.id.asc())
            ).scalars().first()
            if company is None:
                raise

    # Backfill LinkedIn URL if we learned it from a job.
    if linkedin_url and not company.linkedin_url:
        company.linkedin_url = linkedin_url

    return company
