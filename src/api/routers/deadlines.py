from uuid import UUID
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_db
from src.api.dependencies import get_tenant_id
from src.services.deadlines_service import DeadlinesService

router = APIRouter(prefix="/api/v1/deadlines", tags=["Deadlines & Escalations"])

@router.post("/sweep")
async def trigger_deadline_sweep(
    tenant_id: Optional[UUID] = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    res = await DeadlinesService.sweep_deadlines(db, tenant_id)
    return res
