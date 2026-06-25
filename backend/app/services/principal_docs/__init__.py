"""Principal document ingestion + retrieval (the principal's context base)."""
from app.services.principal_docs.ingest import (
    DEFAULT_DOCS_ROOT,
    build_dossier_summary,
    build_principal_dossier,
    ingest_principal_docs,
    retrieve_relevant_proof_points,
)

__all__ = [
    "DEFAULT_DOCS_ROOT",
    "build_dossier_summary",
    "build_principal_dossier",
    "ingest_principal_docs",
    "retrieve_relevant_proof_points",
]
