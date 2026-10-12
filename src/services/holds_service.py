from uuid import UUID
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from src.models.hold import LegalHold
from src.models.request import ErasureRequest
from src.services.ledger_service import LedgerService

class HoldsService:
    @classmethod
    async def place_hold(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        subject_id: UUID,
        reason: str,
        placed_by: str,
        expires_in_days: Optional[int] = None
    ) -> LegalHold:
        expires_at = (datetime.utcnow() + timedelta(days=expires_in_days)) if expires_in_days else None

        hold = LegalHold(
            tenant_id=tenant_id,
            subject_id=subject_id,
            reason=reason,
            placed_by=placed_by,
            expires_at=expires_at
        )
        session.add(hold)
        await session.flush()

        # If an active erasure request is in-flight, immediately freeze it into HELD
        r_stmt = select(ErasureRequest).where(
            ErasureRequest.tenant_id == tenant_id,
            ErasureRequest.subject_id == subject_id,
            ErasureRequest.state.not_in(["COMPLETED", "CANCELLED", "PARTIALLY_COMPLETED"])
        )
        r_res = await session.execute(r_stmt)
        active_req = r_res.scalar_one_or_none()
        if active_req:
            active_req.state = "HELD"
            await LedgerService.append_event(
                session=session,
                tenant_id=tenant_id,
                request_id=active_req.id,
                event_type="LEGAL_HOLD_PLACED",
                payload={
                    "hold_id": str(hold.id),
                    "reason": reason,
                    "placed_by": placed_by
                }
            )

        await session.commit()
        await session.refresh(hold)
        return hold

    @classmethod
    async def list_holds(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        subject_id: Optional[UUID] = None
    ) -> List[LegalHold]:
        stmt = select(LegalHold).where(
            LegalHold.tenant_id == tenant_id,
            LegalHold.released_at.is_(None)
        )
        if subject_id:
            stmt = stmt.where(LegalHold.subject_id == subject_id)
        stmt = stmt.order_by(LegalHold.placed_at.desc())
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def release_hold(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        hold_id: UUID,
        released_by: str,
        release_reason: str
    ) -> LegalHold:
        h_stmt = select(LegalHold).where(
            LegalHold.tenant_id == tenant_id,
            LegalHold.id == hold_id
        )
        h_res = await session.execute(h_stmt)
        hold = h_res.scalar_one_or_none()
        if not hold:
            raise ValueError(f"Legal hold {hold_id} not found")

        hold.released_at = datetime.utcnow()
        hold.released_by = released_by
        hold.release_reason = release_reason

        # Check if subject has other active holds remaining
        other_stmt = select(LegalHold).where(
            LegalHold.tenant_id == tenant_id,
            LegalHold.subject_id == hold.subject_id,
            LegalHold.released_at.is_(None),
            LegalHold.id != hold_id
        )
        other_res = await session.execute(other_stmt)
        remaining = other_res.scalars().all()

        if not remaining:
            # Resume any frozen request
            r_stmt = select(ErasureRequest).where(
                ErasureRequest.tenant_id == tenant_id,
                ErasureRequest.subject_id == hold.subject_id,
                ErasureRequest.state == "HELD"
            )
            r_res = await session.execute(r_stmt)
            held_req = r_res.scalar_one_or_none()
            if held_req:
                held_req.state = "PLANNED"
                await LedgerService.append_event(
                    session=session,
                    tenant_id=tenant_id,
                    request_id=held_req.id,
                    event_type="LEGAL_HOLD_RELEASED",
                    payload={
                        "hold_id": str(hold_id),
                        "released_by": released_by,
                        "release_reason": release_reason
                    }
                )

        await session.commit()
        await session.refresh(hold)
        return hold
