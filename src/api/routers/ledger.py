from uuid import UUID
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_db
from src.api.dependencies import require_tenant_id
from src.services.ledger_service import LedgerService

router = APIRouter(prefix="/api/v1/ledger", tags=["Erasure Ledger"])

@router.get("")
async def query_ledger(
    request_id: Optional[UUID] = Query(None),
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    events = await LedgerService.get_events(db, tenant_id, request_id)
    return [
        {
            "id": str(e.id),
            "tenant_id": str(e.tenant_id),
            "seq": e.seq,
            "request_id": str(e.request_id),
            "event_type": e.event_type,
            "payload": e.payload,
            "prev_hash": e.prev_hash,
            "hash": e.hash,
            "created_at": e.created_at.isoformat()
        }
        for e in events
    ]

@router.get("/verify")
async def verify_ledger(
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    result = await LedgerService.verify_chain(db, tenant_id)
    return result
