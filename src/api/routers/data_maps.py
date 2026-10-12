from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_db
from src.api.dependencies import require_tenant_id
from src.api.schemas import DataMapUpload
from src.services.data_map_service import DataMapService

router = APIRouter(prefix="/api/v1/data-maps", tags=["Data Maps"])

@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_data_map(
    data: DataMapUpload,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        dm_version = await DataMapService.upload_data_map(
            session=db,
            tenant_id=tenant_id,
            version=data.version,
            document=data.document
        )
        return {
            "id": str(dm_version.id),
            "tenant_id": str(dm_version.tenant_id),
            "version": dm_version.version,
            "status": dm_version.status,
            "document": dm_version.document,
            "created_at": dm_version.created_at.isoformat()
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e)
        )

@router.post("/{version_id}/publish")
async def publish_data_map(
    version_id: UUID,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        published = await DataMapService.publish_data_map(
            session=db,
            tenant_id=tenant_id,
            version_id=version_id
        )
        return {
            "id": str(published.id),
            "tenant_id": str(published.tenant_id),
            "version": published.version,
            "status": published.status,
            "published_at": published.published_at.isoformat() if published.published_at else None
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )

@router.get("/active")
async def get_active_data_map(
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    active_dm = await DataMapService.get_active_data_map(db, tenant_id)
    if not active_dm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active Data Map found for tenant"
        )
    return {
        "id": str(active_dm.id),
        "tenant_id": str(active_dm.tenant_id),
        "version": active_dm.version,
        "status": active_dm.status,
        "document": active_dm.document,
        "published_at": active_dm.published_at.isoformat() if active_dm.published_at else None
    }
