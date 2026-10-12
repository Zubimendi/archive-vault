from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_db
from src.api.dependencies import require_tenant_id
from src.api.schemas import CoverageScan, CoverageResolve
from src.services.coverage_service import CoverageService

router = APIRouter(prefix="/api/v1/coverage", tags=["Coverage Drift Monitor"])

@router.post("/scan")
async def trigger_coverage_scan(
    data: CoverageScan,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    res = await CoverageService.scan_drift(
        session=db,
        tenant_id=tenant_id,
        store_id=data.store_id
    )
    return res

@router.get("/findings")
async def list_coverage_findings(
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    findings = await CoverageService.get_findings(db, tenant_id)
    return [
        {
            "id": str(f.id),
            "tenant_id": str(f.tenant_id),
            "store_id": f.store_id,
            "object_name": f.object_name,
            "column_name": f.column_name,
            "finding_type": f.finding_type,
            "severity": f.severity,
            "detected_at": f.detected_at.isoformat(),
            "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None,
            "resolution": f.resolution,
            "reviewer": f.reviewer,
            "notes": f.notes
        }
        for f in findings
    ]

@router.patch("/findings/{finding_id}")
async def resolve_coverage_finding(
    finding_id: UUID,
    data: CoverageResolve,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        finding = await CoverageService.resolve_finding(
            session=db,
            tenant_id=tenant_id,
            finding_id=finding_id,
            resolution=data.resolution,
            reviewer=data.reviewer,
            notes=data.notes
        )
        return {
            "id": str(finding.id),
            "resolution": finding.resolution,
            "reviewer": finding.reviewer,
            "notes": finding.notes,
            "resolved_at": finding.resolved_at.isoformat() if finding.resolved_at else None,
            "status": "RESOLVED"
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
