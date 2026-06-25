"""Index a principal's context documents (incremental).

Usage:
    python -m scripts.ingest_principal_docs --principal-id 1
    python -m scripts.ingest_principal_docs --principal-id 1 --force

Drop files (.pdf, .docx, .txt, .md) into:
    backend/data/principal_docs/<principal-name-slug>/

Only new or changed files are read + sent to the LLM; unchanged files are skipped.
"""
from __future__ import annotations

import argparse
import json

from app.db.session import SessionLocal
from app.models.principal import Principal
from app.services.principal_docs import ingest_principal_docs


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest principal context documents")
    parser.add_argument("--principal-id", type=int, required=True)
    parser.add_argument(
        "--force", action="store_true", help="Re-index even unchanged files"
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        principal = db.get(Principal, args.principal_id)
        if not principal:
            raise SystemExit(f"Principal {args.principal_id} not found")
        summary = ingest_principal_docs(
            db, principal.id, principal.name, force=args.force
        )
        print(json.dumps(summary, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
