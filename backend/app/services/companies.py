"""Company helpers: get-or-create with normalized-name dedup."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
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

    company = db.execute(
        select(Company).where(Company.normalized_name == normalized)
    ).scalar_one_or_none()

    if company is None:
        company = Company(name=name.strip(), normalized_name=normalized)
        db.add(company)
        db.flush()  # assign an id without committing the whole transaction

    # Backfill LinkedIn URL if we learned it from a job.
    if linkedin_url and not company.linkedin_url:
        company.linkedin_url = linkedin_url

    return company
