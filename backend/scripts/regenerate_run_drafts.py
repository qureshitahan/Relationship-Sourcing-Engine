#!/usr/bin/env python3
"""Regenerate all editable email drafts for a discovery run (CLI)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.schemas.requests import EmailRegenerateRunRequest
from app.api.routes.emails import regenerate_run_drafts


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate drafts for a discovery run")
    parser.add_argument("run_id", type=int, help="Discovery run ID")
    parser.add_argument("--principal-id", type=int, default=None)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        payload = EmailRegenerateRunRequest(
            discovery_run_id=args.run_id,
            principal_id=args.principal_id,
            only_statuses=["draft", "approved"],
        )
        result = regenerate_run_drafts(payload, db)
        print(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
