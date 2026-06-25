"""Principal (executive profile) CRUD endpoints."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import AuditAction
from app.models.principal import Principal
from app.models.principal_document import PrincipalDocument
from app.schemas.entities import Page, PrincipalOut
from app.schemas.requests import PrincipalRequest
from app.services.audit import log_action
from app.services.principal_docs import ingest_principal_docs
from app.services.principal_docs.ingest import (
    SUPPORTED_SUFFIXES,
    build_dossier_summary,
    ingest_single_document,
    principal_docs_dir,
)

router = APIRouter(prefix="/principals", tags=["principals"])


@router.get("", response_model=Page[PrincipalOut])
def list_principals(
    db: Session = Depends(get_db),
    active: Optional[bool] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    query = select(Principal)
    count_query = select(func.count()).select_from(Principal)
    if active is not None:
        query = query.where(Principal.is_active.is_(active))
        count_query = count_query.where(Principal.is_active.is_(active))
    query = query.order_by(Principal.created_at.desc()).limit(limit).offset(offset)
    items = db.execute(query).scalars().all()
    total = db.execute(count_query).scalar_one()
    return Page[PrincipalOut](items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=PrincipalOut, status_code=201)
def create_principal(payload: PrincipalRequest, db: Session = Depends(get_db)):
    principal = Principal(
        name=payload.name,
        headline=payload.headline,
        linkedin_url=payload.linkedin_url,
        phone=payload.phone,
        objective=payload.objective,
        document_focus=payload.document_focus,
        bio=payload.bio,
        background=payload.background,
        focus_areas=payload.focus_areas,
        target_sectors=payload.target_sectors,
        investment_themes=payload.investment_themes,
        acquisition_themes=payload.acquisition_themes,
        target_titles=payload.target_titles,
        target_seniorities=payload.target_seniorities,
        geographies=payload.geographies,
        opportunity_types=payload.opportunity_types,
        value_props=payload.value_props,
        is_active=payload.is_active if payload.is_active is not None else True,
    )
    db.add(principal)
    db.flush()
    log_action(
        db,
        AuditAction.PRINCIPAL,
        entity_type="principal",
        entity_id=principal.id,
        summary=f"Created principal {principal.name}",
    )
    db.commit()
    db.refresh(principal)
    return principal


@router.get("/{principal_id}", response_model=PrincipalOut)
def get_principal(principal_id: int, db: Session = Depends(get_db)):
    principal = db.get(Principal, principal_id)
    if not principal:
        raise HTTPException(status_code=404, detail="Principal not found")
    return principal


@router.put("/{principal_id}", response_model=PrincipalOut)
def update_principal(
    principal_id: int, payload: PrincipalRequest, db: Session = Depends(get_db)
):
    principal = db.get(Principal, principal_id)
    if not principal:
        raise HTTPException(status_code=404, detail="Principal not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(principal, field, value)
    log_action(
        db,
        AuditAction.PRINCIPAL,
        entity_type="principal",
        entity_id=principal.id,
        summary=f"Updated principal {principal.name}",
    )
    db.commit()
    db.refresh(principal)
    return principal


@router.get("/{principal_id}/dossier")
def get_principal_dossier(principal_id: int, db: Session = Depends(get_db)):
    """Aggregated view of what document indexing extracted — for the Principals UI."""
    principal = db.get(Principal, principal_id)
    if not principal:
        raise HTTPException(status_code=404, detail="Principal not found")
    return build_dossier_summary(db, principal.id)


@router.get("/{principal_id}/documents")
def list_principal_documents(principal_id: int, db: Session = Depends(get_db)):
    principal = db.get(Principal, principal_id)
    if not principal:
        raise HTTPException(status_code=404, detail="Principal not found")
    docs = (
        db.query(PrincipalDocument)
        .filter(PrincipalDocument.principal_id == principal_id)
        .order_by(PrincipalDocument.indexed_at.desc().nullslast())
        .all()
    )
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "doc_type": d.doc_type,
            "status": d.status,
            "char_count": d.char_count,
            "summary": d.summary,
            "key_facts": d.key_facts or [],
            "themes": d.themes or [],
            "relevance_score": d.relevance_score,
            "relevance_note": d.relevance_note,
            "indexed_by": d.indexed_by,
            "indexed_at": d.indexed_at,
        }
        for d in docs
    ]


@router.post("/{principal_id}/documents/upload")
async def upload_documents(
    principal_id: int,
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Save uploaded files into the principal's docs folder.

    Does NOT index them — call ``/documents/index-file`` per file (or
    ``/documents/ingest`` for batch) so the UI can show per-file progress.
    """
    principal = db.get(Principal, principal_id)
    if not principal:
        raise HTTPException(status_code=404, detail="Principal not found")

    folder = principal_docs_dir(principal.name)
    folder.mkdir(parents=True, exist_ok=True)

    saved: List[str] = []
    rejected: List[dict] = []
    for upload in files:
        name = Path(upload.filename or "").name
        if not name:
            continue
        suffix = Path(name).suffix.lower()
        if suffix == ".doc":
            rejected.append({
                "file": name,
                "reason": "Legacy .doc not supported — open in Word and Save As .docx",
            })
            continue
        if suffix not in SUPPORTED_SUFFIXES:
            rejected.append({"file": name, "reason": f"Unsupported type ({suffix})"})
            continue
        dest = folder / name
        dest.write_bytes(await upload.read())
        saved.append(name)

    log_action(
        db,
        AuditAction.PRINCIPAL,
        entity_type="principal",
        entity_id=principal.id,
        summary=f"Uploaded {len(saved)} document(s) for {principal.name}.",
    )
    db.commit()
    return {
        "folder": str(folder),
        "uploaded": saved,
        "rejected": rejected,
        "message": (
            f"{len(saved)} file(s) saved. Index them to extract proof points."
            if saved
            else "No files saved."
        ),
    }


@router.post("/{principal_id}/documents/index-file")
def index_one_document(
    principal_id: int,
    filename: str = Query(..., description="Filename in the principal docs folder"),
    force: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Index a single document (Claude extraction). One call per file for UI progress."""
    principal = db.get(Principal, principal_id)
    if not principal:
        raise HTTPException(status_code=404, detail="Principal not found")
    result = ingest_single_document(
        db, principal.id, principal.name, filename, force=force
    )
    if result.get("action") == "failed":
        raise HTTPException(status_code=400, detail=result.get("error", "Indexing failed"))
    return result


@router.delete("/{principal_id}/documents/{document_id}", status_code=204)
def delete_document(principal_id: int, document_id: int, db: Session = Depends(get_db)):
    doc = db.get(PrincipalDocument, document_id)
    if not doc or doc.principal_id != principal_id:
        raise HTTPException(status_code=404, detail="Document not found")
    principal = db.get(Principal, principal_id)
    if principal:
        path = principal_docs_dir(principal.name) / doc.filename
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    db.delete(doc)
    db.commit()
    return None


@router.post("/{principal_id}/documents/ingest")
def ingest_documents(
    principal_id: int,
    force: bool = Query(False, description="Re-index even unchanged files"),
    db: Session = Depends(get_db),
):
    """Index new/changed documents in data/principal_docs/<principal>/.

    Incremental: unchanged files (same content hash) are skipped, so adding new
    files does not re-process the existing corpus.
    """
    principal = db.get(Principal, principal_id)
    if not principal:
        raise HTTPException(status_code=404, detail="Principal not found")
    summary = ingest_principal_docs(
        db, principal.id, principal.name, force=force
    )
    log_action(
        db,
        AuditAction.PRINCIPAL,
        entity_type="principal",
        entity_id=principal.id,
        summary=(
            f"Ingested docs for {principal.name}: "
            f"{summary.get('indexed')} new, {summary.get('updated')} updated, "
            f"{summary.get('skipped')} skipped, {summary.get('failed')} failed."
        ),
    )
    db.commit()
    return summary


@router.delete("/{principal_id}", status_code=204)
def deactivate_principal(principal_id: int, db: Session = Depends(get_db)):
    principal = db.get(Principal, principal_id)
    if not principal:
        raise HTTPException(status_code=404, detail="Principal not found")
    principal.is_active = False
    db.commit()
    return None
