from uuid import UUID
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_db
from src.api.dependencies import get_tenant_id
from src.services.coverage_service import CoverageService

router = APIRouter(tags=["Health"])

@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "ArchiveVault",
        "version": "1.0.0"
    }

@router.get("/api/v1/coverage/health")
async def coverage_health(
    tenant_id: Optional[UUID] = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    health = await CoverageService.get_health(db, tenant_id)
    return health
