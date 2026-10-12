from uuid import UUID
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.config import settings
from src.models.subject import Subject
from src.models.tenant import Tenant
from src.crypto.envelope import EnvelopeCrypto

class SubjectService:
    @staticmethod
    async def register_subject(
        session: AsyncSession,
        tenant_id: UUID,
        identifier_type: str,
        identifier_value: str,
        pepper_context: Optional[str] = None
    ) -> Subject:
        # Retrieve tenant to obtain KEK reference
        tenant_stmt = select(Tenant).where(Tenant.id == tenant_id)
        res = await session.execute(tenant_stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} not found")

        # Derive tenant KEK and generate per-subject AES-256 DEK
        tenant_kek = EnvelopeCrypto.derive_tenant_kek(tenant.kek_ref, settings.MASTER_KEK_SECRET)
        subject_dek = EnvelopeCrypto.generate_dek()
        wrapped_dek, iv = EnvelopeCrypto.wrap_dek(tenant_kek, subject_dek)

        # Persist subject with Zero PII (identifier_value is never written to DB)
        subject = Subject(
            tenant_id=tenant_id,
            wrapped_dek=wrapped_dek,
            dek_iv=iv,
            status="ACTIVE"
        )
        session.add(subject)
        await session.commit()
        await session.refresh(subject)
        return subject

    @staticmethod
    async def get_subject(
        session: AsyncSession,
        tenant_id: UUID,
        subject_id: UUID
    ) -> Optional[Subject]:
        stmt = select(Subject).where(
            Subject.tenant_id == tenant_id,
            Subject.id == subject_id
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()
