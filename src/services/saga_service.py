from uuid import UUID
from datetime import datetime
from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from src.models.request import ErasureRequest
from src.models.step import ErasureStep
from src.models.subject import Subject
from src.data_map.models import ActionEnum
from src.connectors.postgres import PostgresConnector
from src.connectors.webhook import WebhookConnector
from src.connectors.manual import ManualConnector
from src.services.ledger_service import LedgerService

class SagaService:
    POST_PIVOT_STATES = {"PURGING", "PROPAGATING", "VERIFYING", "COMPLETED"}

    @classmethod
    async def cancel_request(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        request_id: UUID,
        cancellation_reason: str,
        actor: Optional[str] = None
    ) -> Dict[str, Any]:
        stmt = select(ErasureRequest).where(
            ErasureRequest.tenant_id == tenant_id,
            ErasureRequest.id == request_id
        ).with_for_update()
        res = await session.execute(stmt)
        req = res.scalar_one_or_none()
        if not req:
            raise ValueError(f"Erasure request {request_id} not found")

        # Architectural Invariant: Cancellation post-pivot is forbidden (point of no return)
        if req.state in cls.POST_PIVOT_STATES:
            raise ValueError(
                f"Cannot cancel erasure request in '{req.state}' state. "
                "The pivot point has been reached and downstream deletions cannot be undone."
            )

        # Compensate quarantine: revert soft locks on downstream stores
        step_stmt = select(ErasureStep).where(
            ErasureStep.request_id == request_id,
            ErasureStep.phase == "QUARANTINE"
        )
        s_res = await session.execute(step_stmt)
        quarantine_steps = s_res.scalars().all()

        pg_connector = PostgresConnector()
        try:
            for q_step in quarantine_steps:
                if q_step.state in ("COMPLETED", "PENDING"):
                    subj_key = (q_step.evidence or {}).get("subject_key", "id")
                    await pg_connector.compensate_quarantine(
                        tenant_id=tenant_id,
                        subject_id=req.subject_id,
                        dataset=q_step.dataset,
                        subject_key=subj_key
                    )
                    q_step.state = "CANCELLED"
        finally:
            await pg_connector.close()

        # Mark all pending steps as CANCELLED
        all_steps_stmt = select(ErasureStep).where(
            ErasureStep.request_id == request_id,
            ErasureStep.state == "PENDING"
        )
        as_res = await session.execute(all_steps_stmt)
        for step in as_res.scalars().all():
            step.state = "CANCELLED"

        req.state = "CANCELLED"
        req.cancellation_reason = cancellation_reason

        # Append to Zero-PII tamper-evident ledger
        await LedgerService.append_event(
            session=session,
            tenant_id=tenant_id,
            request_id=request_id,
            event_type="REQUEST_CANCELLED",
            payload={
                "reason": cancellation_reason,
                "actor": actor or "system",
                "compensated_steps": len(quarantine_steps)
            }
        )

        await session.commit()
        return {
            "request_id": str(request_id),
            "state": "CANCELLED",
            "cancellation_reason": cancellation_reason,
            "compensated_steps": len(quarantine_steps)
        }

    @classmethod
    async def pivot_to_purge(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        request_id: UUID,
        bypass_quarantine_window: bool = False,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        stmt = select(ErasureRequest).where(
            ErasureRequest.tenant_id == tenant_id,
            ErasureRequest.id == request_id
        ).with_for_update()
        res = await session.execute(stmt)
        req = res.scalar_one_or_none()
        if not req:
            raise ValueError(f"Erasure request {request_id} not found")

        now = datetime.utcnow()
        if not bypass_quarantine_window and req.quarantine_until and now < req.quarantine_until:
            raise ValueError(f"Quarantine window active until {req.quarantine_until.isoformat()}")

        req.state = "PURGING"

        await LedgerService.append_event(
            session=session,
            tenant_id=tenant_id,
            request_id=request_id,
            event_type="PIVOT_TO_PURGE",
            payload={
                "bypass_quarantine_window": bypass_quarantine_window,
                "reason": reason or "Quarantine window ended"
            }
        )

        await session.commit()
        return {
            "request_id": str(request_id),
            "state": "PURGING",
            "message": "Pivot point reached. Request is now irreversible."
        }

    @classmethod
    async def execute_next_step(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        request_id: UUID
    ) -> Dict[str, Any]:
        # Lock request
        r_stmt = select(ErasureRequest).where(
            ErasureRequest.tenant_id == tenant_id,
            ErasureRequest.id == request_id
        ).with_for_update()
        r_res = await session.execute(r_stmt)
        req = r_res.scalar_one_or_none()
        if not req:
            raise ValueError(f"Erasure request {request_id} not found")

        # Find next pending step
        s_stmt = (
            select(ErasureStep)
            .where(
                ErasureStep.request_id == request_id,
                ErasureStep.state == "PENDING"
            )
            .order_by(
                ErasureStep.phase.desc(), # 'QUARANTINE' first, then 'PURGE', then 'PROPAGATE'
                ErasureStep.created_at.asc()
            )
            .with_for_update()
        )
        s_res = await session.execute(s_stmt)
        pending_steps = list(s_res.scalars().all())

        if not pending_steps:
            # Check if all steps are completed
            all_s_stmt = select(ErasureStep).where(ErasureStep.request_id == request_id)
            all_res = await session.execute(all_s_stmt)
            all_steps = list(all_res.scalars().all())
            if all(s.state == "COMPLETED" for s in all_steps):
                req.state = "VERIFYING"
                await session.commit()
            return {
                "request_id": str(request_id),
                "state": req.state,
                "message": "No pending steps remaining"
            }

        step = pending_steps[0]

        # Auto-pivot check if all quarantine steps are done or window has expired
        if step.phase in ("PURGE", "PROPAGATE") and req.state in ("QUARANTINED", "PLANNED"):
            req.state = "PURGING"

        evidence_meta = step.evidence or {}
        subject_key = evidence_meta.get("subject_key", "id")
        action_enum = ActionEnum(step.action) if step.action in ActionEnum.__members__ else ActionEnum.DELETE

        # Execute step via appropriate connector
        if step.phase == "QUARANTINE":
            pg = PostgresConnector()
            try:
                result = await pg.quarantine(
                    tenant_id=tenant_id,
                    subject_id=req.subject_id,
                    dataset=step.dataset,
                    subject_key=subject_key,
                    cutoff_at=req.cutoff_at
                )
            finally:
                await pg.close()

        elif step.phase == "PURGE":
            if action_enum == ActionEnum.CRYPTO_SHRED:
                # 1. Zeroize the subject's wrapped DEK in the subject table (instant crypto shred)
                s_update = (
                    update(Subject)
                    .where(Subject.id == req.subject_id)
                    .values(wrapped_dek=b"\x00" * 32, status="ERASED")
                )
                await session.execute(s_update)
                # 2. Inform downstream store (ciphertext remains intact, but unreadable)
                pg = PostgresConnector()
                try:
                    result = await pg.erase(
                        tenant_id=tenant_id,
                        subject_id=req.subject_id,
                        dataset=step.dataset,
                        subject_key=subject_key,
                        action=action_enum,
                        cutoff_at=req.cutoff_at
                    )
                finally:
                    await pg.close()

            else:
                pg = PostgresConnector()
                try:
                    result = await pg.erase(
                        tenant_id=tenant_id,
                        subject_id=req.subject_id,
                        dataset=step.dataset,
                        subject_key=subject_key,
                        action=action_enum,
                        cutoff_at=req.cutoff_at,
                        anonymize_rules=evidence_meta.get("anonymize_rules")
                    )
                finally:
                    await pg.close()

        elif step.phase == "PROPAGATE":
            connector_type = evidence_meta.get("connector", "webhook")
            if connector_type == "webhook":
                wh = WebhookConnector()
                result = await wh.erase(
                    tenant_id=tenant_id,
                    subject_id=req.subject_id,
                    dataset=step.dataset,
                    subject_key=subject_key,
                    action=action_enum,
                    cutoff_at=req.cutoff_at
                )
            else:
                mc = ManualConnector()
                result = await mc.erase(
                    tenant_id=tenant_id,
                    subject_id=req.subject_id,
                    dataset=step.dataset,
                    subject_key=subject_key,
                    action=action_enum,
                    cutoff_at=req.cutoff_at
                )

        # Update step status
        step.state = "COMPLETED"
        step.attempts += 1
        step.evidence = {**evidence_meta, **result}

        # Append step completion to ledger
        await LedgerService.append_event(
            session=session,
            tenant_id=tenant_id,
            request_id=request_id,
            event_type="STEP_COMPLETED",
            payload={
                "step_id": str(step.id),
                "phase": step.phase,
                "action": step.action,
                "dataset": step.dataset,
                "store_id": step.store_id,
                "outcome": result
            }
        )

        # Check if this was the last pending step
        if len(pending_steps) == 1:
            req.state = "VERIFYING"

        await session.commit()

        return {
            "step_id": str(step.id),
            "store_id": step.store_id,
            "dataset": step.dataset,
            "phase": step.phase,
            "action": step.action,
            "state": step.state,
            "result": result,
            "remaining_pending": len(pending_steps) - 1,
            "request_state": req.state
        }
