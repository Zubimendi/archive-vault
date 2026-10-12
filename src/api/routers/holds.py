from uuid import UUID
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_db
from src.api.dependencies import require_tenant_id
from src.api.schemas import LegalHoldCreate, LegalHoldRelease
from src.services.holds_service import HoldsService

router = APIRouter(prefix="/api/v1/holds", tags=["Legal Holds"])

@router.post("", status_code=status.HTTP_201_CREATED)
async def place_legal_hold(
    data: LegalHoldCreate,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        hold = await HoldsService.place_hold(
            session=db,
            tenant_id=tenant_id,
            subject_id=data.subject_id,
            reason=data.reason,
            placed_by=data.placed_by,
            expires_in_days=data.expires_in_days
        )
        return {
            "id": str(hold.id),
            "tenant_id": str(hold.tenant_id),
            "subject_id": str(hold.subject_id),
            "reason": hold.reason,
            "placed_by": hold.placed_by,
            "placed_at": hold.placed_at.isoformat(),
            "expires_at": hold.expires_at.isoformat() if hold.expires_at else None
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("")
async def list_legal_holds(
    subject_id: Optional[UUID] = Query(None),
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    holds = await HoldsService.list_holds(db, tenant_id, subject_id)
    return [
        {
            "id": str(h.id),
            "tenant_id": str(h.tenant_id),
            "subject_id": str(h.subject_id),
            "reason": h.reason,
            "placed_by": h.placed_by,
            "placed_at": h.placed_at.isoformat(),
            "expires_at": h.expires_at.isoformat() if h.expires_at else None,
            "released_at": h.released_at.isoformat() if h.released_at else None
        }
        for h in holds
    ]

@router.post("/{hold_id}/release")
async def release_legal_hold(
    hold_id: UUID,
    data: LegalHoldRelease,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        hold = await HoldsService.release_hold(
            session=db,
            tenant_id=tenant_id,
            hold_id=hold_id,
            released_by=data.released_by,
            release_reason=data.release_reason
        )
        return {
            "id": str(hold.id),
            "released_by": hold.released_by,
            "release_reason": hold.release_reason,
            "released_at": hold.released_at.isoformat() if hold.released_at else None,
            "status": "RELEASED"
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
