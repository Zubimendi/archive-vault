from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_db
from src.api.schemas import TenantCreate, TenantOffboard
from src.services.tenant_service import TenantService

router = APIRouter(prefix="/api/v1/tenants", tags=["Tenants"])

@router.post("", status_code=status.HTTP_201_CREATED)
async def onboard_tenant(
    data: TenantCreate,
    db: AsyncSession = Depends(get_db)
):
    tenant = await TenantService.create_tenant(
        session=db,
        name=data.name,
        regime_profile=data.regime_profile,
        kek_ref=data.kek_ref
    )
    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "regime_profile": tenant.regime_profile,
        "kek_ref": tenant.kek_ref,
        "created_at": tenant.created_at.isoformat()
    }

@router.get("/{tenant_id}")
async def get_tenant(
    tenant_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    tenant = await TenantService.get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant {tenant_id} not found"
        )
    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "regime_profile": tenant.regime_profile,
        "kek_ref": tenant.kek_ref,
        "created_at": tenant.created_at.isoformat()
    }

@router.post("/{tenant_id}/offboard")
async def offboard_tenant(
    tenant_id: UUID,
    data: TenantOffboard,
    db: AsyncSession = Depends(get_db)
):
    try:
        res = await TenantService.offboard_tenant(
            session=db,
            tenant_id=tenant_id,
            confirmation=data.confirmation,
            reason=data.reason
        )
        return res
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
