from uuid import UUID
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from src.models.tenant import Tenant
from src.models.subject import Subject

class TenantService:
    @staticmethod
    async def create_tenant(
        session: AsyncSession,
        name: str,
        regime_profile: str = "GDPR",
        kek_ref: Optional[str] = None
    ) -> Tenant:
        kek_ref_val = kek_ref or f"arn:aws:kms:eu-central-1:vault:key/{name.lower().replace(' ', '-')}-kek"
        tenant = Tenant(
            name=name,
            regime_profile=regime_profile,
            kek_ref=kek_ref_val
        )
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
        return tenant

    @staticmethod
    async def get_tenant(session: AsyncSession, tenant_id: UUID) -> Optional[Tenant]:
        stmt = select(Tenant).where(Tenant.id == tenant_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def offboard_tenant(
        session: AsyncSession,
        tenant_id: UUID,
        confirmation: str,
        reason: str
    ) -> Dict[str, Any]:
        if confirmation != "DESTROY_ALL_TENANT_DATA":
            raise ValueError("Confirmation must be exactly 'DESTROY_ALL_TENANT_DATA'")

        tenant = await TenantService.get_tenant(session, tenant_id)
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} not found")

        # Zeroize all subject DEKs under this tenant (instant cryptographic shred of tenant data)
        stmt = (
            update(Subject)
            .where(Subject.tenant_id == tenant_id)
            .values(
                wrapped_dek=b"\x00" * 32,
                status="ERASED"
            )
        )
        await session.execute(stmt)
        await session.commit()

        return {
            "tenant_id": str(tenant_id),
            "status": "OFFBOARDED",
            "reason": reason,
            "message": "Tenant offboarded and all wrapped cryptographic keys shredded."
        }
