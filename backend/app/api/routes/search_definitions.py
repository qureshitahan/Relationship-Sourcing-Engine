"""ICP search definition CRUD endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import AuditAction
from app.models.principal import Principal
from app.models.search_definition import SearchDefinition
from app.schemas.entities import Page, SearchDefinitionOut
from app.schemas.requests import SearchDefinitionRequest
from app.services.audit import log_action

router = APIRouter(prefix="/search-definitions", tags=["search-definitions"])


@router.get("", response_model=Page[SearchDefinitionOut])
def list_search_definitions(
    db: Session = Depends(get_db),
    principal_id: Optional[int] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    query = select(SearchDefinition)
    count_query = select(func.count()).select_from(SearchDefinition)
    if principal_id is not None:
        query = query.where(SearchDefinition.principal_id == principal_id)
        count_query = count_query.where(SearchDefinition.principal_id == principal_id)
    query = query.order_by(SearchDefinition.created_at.desc()).limit(limit).offset(offset)
    items = db.execute(query).scalars().all()
    total = db.execute(count_query).scalar_one()
    return Page[SearchDefinitionOut](items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=SearchDefinitionOut, status_code=201)
def create_search_definition(
    payload: SearchDefinitionRequest, db: Session = Depends(get_db)
):
    if not db.get(Principal, payload.principal_id):
        raise HTTPException(status_code=404, detail="Principal not found")
    definition = SearchDefinition(**payload.model_dump())
    db.add(definition)
    db.flush()
    log_action(
        db,
        AuditAction.SEARCH_DEFINITION,
        entity_type="search_definition",
        entity_id=definition.id,
        summary=f"Created search definition {definition.name}",
    )
    db.commit()
    db.refresh(definition)
    return definition


@router.get("/{definition_id}", response_model=SearchDefinitionOut)
def get_search_definition(definition_id: int, db: Session = Depends(get_db)):
    definition = db.get(SearchDefinition, definition_id)
    if not definition:
        raise HTTPException(status_code=404, detail="Search definition not found")
    return definition


@router.put("/{definition_id}", response_model=SearchDefinitionOut)
def update_search_definition(
    definition_id: int, payload: SearchDefinitionRequest, db: Session = Depends(get_db)
):
    definition = db.get(SearchDefinition, definition_id)
    if not definition:
        raise HTTPException(status_code=404, detail="Search definition not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(definition, field, value)
    db.commit()
    db.refresh(definition)
    return definition


@router.delete("/{definition_id}", status_code=204)
def delete_search_definition(definition_id: int, db: Session = Depends(get_db)):
    definition = db.get(SearchDefinition, definition_id)
    if not definition:
        raise HTTPException(status_code=404, detail="Search definition not found")
    db.delete(definition)
    db.commit()
    return None
