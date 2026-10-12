from uuid import UUID
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.models.request import ErasureRequest
from src.services.ledger_service import LedgerService

class DeadlinesService:
    TERMINAL_STATES = {"COMPLETED", "CANCELLED", "PARTIALLY_COMPLETED"}

    @classmethod
    async def sweep_deadlines(
        cls,
        session: AsyncSession,
        tenant_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        stmt = select(ErasureRequest).where(ErasureRequest.state.not_in(cls.TERMINAL_STATES))
        if tenant_id:
            stmt = stmt.where(ErasureRequest.tenant_id == tenant_id)
        res = await session.execute(stmt)
        requests = list(res.scalars().all())

        now = datetime.utcnow()
        t14_count = 0
        t7_count = 0
        overdue_count = 0

        for req in requests:
            diff_days = (req.due_at - now).total_seconds() / 86400.0

            if diff_days <= 0:
                if req.state != "OVERDUE":
                    req.state = "OVERDUE"
                    overdue_count += 1
                    await LedgerService.append_event(
                        session=session,
                        tenant_id=req.tenant_id,
                        request_id=req.id,
                        event_type="DEADLINE_OVERDUE",
                        payload={"days_overdue": abs(int(diff_days))}
                    )
            elif diff_days <= 7:
                if req.state not in ("AT_RISK", "OVERDUE"):
                    req.state = "AT_RISK"
                    t7_count += 1
                    await LedgerService.append_event(
                        session=session,
                        tenant_id=req.tenant_id,
                        request_id=req.id,
                        event_type="DEADLINE_WARNING_T_MINUS_7",
                        payload={"days_remaining": int(diff_days)}
                    )
            elif diff_days <= 14:
                t14_count += 1
                await LedgerService.append_event(
                    session=session,
                    tenant_id=req.tenant_id,
                    request_id=req.id,
                    event_type="DEADLINE_WARNING_T_MINUS_14",
                    payload={"days_remaining": int(diff_days)}
                )

        await session.commit()
        return {
            "swept_count": len(requests),
            "t14_warnings": t14_count,
            "at_risk_t7": t7_count,
            "overdue": overdue_count,
            "timestamp": now.isoformat()
        }

    @classmethod
    async def extend_deadline(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        request_id: UUID,
        extension_days: int,
        legal_basis: str,
        subject_notified: bool,
        authorized_by: str
    ) -> Dict[str, Any]:
        stmt = select(ErasureRequest).where(
            ErasureRequest.tenant_id == tenant_id,
            ErasureRequest.id == request_id
        ).with_for_update()
        res = await session.execute(stmt)
        req = res.scalar_one_or_none()
        if not req:
            raise ValueError(f"Erasure request {request_id} not found")

        if extension_days <= 0:
            raise ValueError("Extension days must be positive")
        if not legal_basis:
            raise ValueError("Legal basis must be declared for deadline extension under GDPR/CCPA")

        req.due_at += timedelta(days=extension_days)
        if req.state == "OVERDUE":
            req.state = "PURGING"

        await LedgerService.append_event(
            session=session,
            tenant_id=tenant_id,
            request_id=request_id,
            event_type="DEADLINE_EXTENDED",
            payload={
                "extension_days": extension_days,
                "legal_basis": legal_basis,
                "subject_notified": subject_notified,
                "authorized_by": authorized_by,
                "new_due_at": req.due_at.isoformat()
            }
        )

        await session.commit()
        return {
            "request_id": str(request_id),
            "new_due_at": req.due_at.isoformat(),
            "state": req.state,
            "extension_days": extension_days,
            "legal_basis": legal_basis
        }
