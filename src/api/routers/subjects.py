from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_db
from src.api.dependencies import require_tenant_id
from src.api.schemas import SubjectRegister
from src.services.subject_service import SubjectService

router = APIRouter(prefix="/api/v1/subjects", tags=["Subjects"])

@router.post("", status_code=status.HTTP_201_CREATED)
async def register_subject(
    data: SubjectRegister,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        subject = await SubjectService.register_subject(
            session=db,
            tenant_id=tenant_id,
            identifier_type=data.identifier_type,
            identifier_value=data.identifier_value,
            pepper_context=data.pepper_context
        )
        return {
            "id": str(subject.id),
            "tenant_id": str(subject.tenant_id),
            "status": subject.status,
            "has_wrapped_dek": subject.wrapped_dek is not None,
            "created_at": subject.created_at.isoformat(),
            "message": "Subject registered with wrapped DEK. Raw identifier dropped (Zero-PII)."
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("/{subject_id}")
async def get_subject(
    subject_id: UUID,
    tenant_id: UUID = Depends(require_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    subject = await SubjectService.get_subject(db, tenant_id, subject_id)
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subject {subject_id} not found"
        )
    return {
        "id": str(subject.id),
        "tenant_id": str(subject.tenant_id),
        "status": subject.status,
        "has_wrapped_dek": subject.wrapped_dek is not None and subject.wrapped_dek != b"\x00" * 32,
        "created_at": subject.created_at.isoformat()
    }
