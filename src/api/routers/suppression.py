from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_db
from src.api.dependencies import require_tenant_id
from src.api.schemas import SuppressionCheck, RestoreReplay
from src.services.suppression_service import SuppressionService

router = APIRouter(prefix="/api/v1/suppression", tags=["Suppression & Restore-Replay"])

@router.post("/check")
async def check_suppression(
    data: SuppressionCheck,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    res = await SuppressionService.check_suppression(
        session=db,
        tenant_id=tenant_id,
        identifier=data.identifier
    )
    return res

@router.post("/restore-replay")
async def trigger_restore_replay(
    data: RestoreReplay,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    res = await SuppressionService.restore_replay(
        session=db,
        tenant_id=tenant_id,
        target_store_id=data.target_store_id,
        notes=data.notes
    )
    return res
