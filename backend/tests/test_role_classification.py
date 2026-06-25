"""Unit tests for prospect role/seniority classification and title scoring."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.enums import RoleCategory
from app.services.contacts import (
    classify_role_category,
    infer_seniority,
    score_contact_title,
)


def test_classify_role_category():
    assert classify_role_category("Chief Executive Officer") == RoleCategory.CEO
    assert classify_role_category("Co-Founder & CEO") == RoleCategory.FOUNDER
    assert classify_role_category("Operating Partner") == RoleCategory.OPERATING_PARTNER
    assert classify_role_category("General Partner") == RoleCategory.INVESTOR
    assert classify_role_category("Board Member") == RoleCategory.BOARD_MEMBER
    assert classify_role_category("VP of Strategy") == RoleCategory.EXECUTIVE
    assert classify_role_category(None) == RoleCategory.OTHER


def test_infer_seniority():
    assert infer_seniority("Founder") == "owner"
    assert infer_seniority("Chief Financial Officer") == "c_suite"
    assert infer_seniority("Managing Partner") == "partner"
    assert infer_seniority(None) is None


def test_score_contact_title_prefers_senior_roles():
    ceo_score, _ = score_contact_title("Chief Executive Officer", None)
    analyst_score, _ = score_contact_title("Analyst", None)
    assert ceo_score > analyst_score
    assert ceo_score >= 90


if __name__ == "__main__":
    test_classify_role_category()
    test_infer_seniority()
    test_score_contact_title_prefers_senior_roles()
    print("All role classification tests passed.")
