from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_db
from src.api.dependencies import require_tenant_id
from src.api.schemas import (
    ErasureRequestCreate,
    ErasureRequestCancel,
    ErasureRequestPivot,
    ErasureRequestExtend
)
from src.services.planner_service import PlannerService
from src.services.saga_service import SagaService
from src.services.verifier_service import VerifierService
from src.services.certificate_service import CertificateService
from src.services.deadlines_service import DeadlinesService

router = APIRouter(prefix="/api/v1/erasure-requests", tags=["Erasure Requests"])

@router.post("", status_code=status.HTTP_201_CREATED)
async def submit_erasure_request(
    data: ErasureRequestCreate,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        req = await PlannerService.create_erasure_request(
            session=db,
            tenant_id=tenant_id,
            subject_id=data.subject_id,
            trigger=data.trigger,
            regime=data.regime,
            quarantine_days=data.quarantine_days
        )
        return {
            "id": str(req.id),
            "tenant_id": str(req.tenant_id),
            "subject_id": str(req.subject_id),
            "trigger": req.trigger,
            "regime": req.regime,
            "data_map_version_id": str(req.data_map_version_id),
            "received_at": req.received_at.isoformat(),
            "due_at": req.due_at.isoformat(),
            "cutoff_at": req.cutoff_at.isoformat(),
            "quarantine_until": req.quarantine_until.isoformat() if req.quarantine_until else None,
            "state": req.state,
            "message": "Erasure request accepted and planned into saga steps."
        }
    except ValueError as e:
        err_msg = str(e)
        if "already in-flight" in err_msg or "Active erasure request" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=err_msg
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg
        )

@router.get("/{id}")
async def get_erasure_request(
    id: UUID,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    req_data = await PlannerService.get_request(db, tenant_id, id)
    if not req_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Erasure request {id} not found"
        )
    return req_data

@router.post("/{id}/cancel")
async def cancel_erasure_request(
    id: UUID,
    data: ErasureRequestCancel,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        res = await SagaService.cancel_request(
            session=db,
            tenant_id=tenant_id,
            request_id=id,
            cancellation_reason=data.cancellation_reason,
            actor=data.actor
        )
        return res
    except ValueError as e:
        err_msg = str(e)
        if "Cannot cancel" in err_msg or "pivot point" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=err_msg
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg
        )

@router.post("/{id}/pivot")
async def pivot_erasure_request(
    id: UUID,
    data: ErasureRequestPivot,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        res = await SagaService.pivot_to_purge(
            session=db,
            tenant_id=tenant_id,
            request_id=id,
            bypass_quarantine_window=data.bypass_quarantine_window,
            reason=data.reason
        )
        return res
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.post("/{id}/execute-step")
async def execute_saga_step(
    id: UUID,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        res = await SagaService.execute_next_step(
            session=db,
            tenant_id=tenant_id,
            request_id=id
        )
        return res
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.post("/{id}/verify")
async def verify_erasure_request(
    id: UUID,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        res = await VerifierService.verify_request(
            session=db,
            tenant_id=tenant_id,
            request_id=id
        )
        return res
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("/{id}/certificate")
async def get_erasure_certificate(
    id: UUID,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        cert = await CertificateService.generate_certificate(
            session=db,
            tenant_id=tenant_id,
            request_id=id
        )
        return cert
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )

@router.post("/{id}/extend")
async def extend_erasure_request(
    id: UUID,
    data: ErasureRequestExtend,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        res = await DeadlinesService.extend_deadline(
            session=db,
            tenant_id=tenant_id,
            request_id=id,
            extension_days=data.extension_days,
            legal_basis=data.legal_basis,
            subject_notified=data.subject_notified,
            authorized_by=data.authorized_by
        )
        return res
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
