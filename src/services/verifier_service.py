from uuid import UUID
from typing import Any, Dict, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.config import settings
from src.models.request import ErasureRequest
from src.models.step import ErasureStep
from src.models.subject import Subject
from src.models.suppression import SuppressionLedger
from src.data_map.models import ActionEnum
from src.connectors.postgres import PostgresConnector
from src.services.ledger_service import LedgerService
from src.crypto.suppression_hmac import compute_suppression_hmac

class VerifierService:
    @classmethod
    async def verify_request(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        request_id: UUID
    ) -> Dict[str, Any]:
        r_stmt = select(ErasureRequest).where(
            ErasureRequest.tenant_id == tenant_id,
            ErasureRequest.id == request_id
        ).with_for_update()
        r_res = await session.execute(r_stmt)
        req = r_res.scalar_one_or_none()
        if not req:
            raise ValueError(f"Erasure request {request_id} not found")

        s_stmt = select(ErasureStep).where(
            ErasureStep.request_id == request_id,
            ErasureStep.phase == "PURGE"
        )
        s_res = await session.execute(s_stmt)
        purge_steps = list(s_res.scalars().all())

        pg = PostgresConnector()
        discrepancies = []

        try:
            for step in purge_steps:
                evidence_meta = step.evidence or {}
                subject_key = evidence_meta.get("subject_key", "id")
                action_enum = ActionEnum(step.action) if step.action in ActionEnum.__members__ else ActionEnum.DELETE

                v_res = await pg.verify(
                    tenant_id=tenant_id,
                    subject_id=req.subject_id,
                    dataset=step.dataset,
                    subject_key=subject_key,
                    cutoff_at=req.cutoff_at,
                    action=action_enum
                )

                if not v_res.get("verified", False):
                    discrepancies.append({
                        "dataset": step.dataset,
                        "action": step.action,
                        "finding": "Remnant records detected",
                        "details": v_res
                    })

                # Check CRYPTO_SHRED subject key zeroization
                if action_enum == ActionEnum.CRYPTO_SHRED:
                    subj_stmt = select(Subject).where(Subject.id == req.subject_id)
                    s_obj = (await session.execute(subj_stmt)).scalar_one_or_none()
                    if s_obj and s_obj.wrapped_dek != b"\x00" * 32 and s_obj.status != "ERASED":
                        discrepancies.append({
                            "dataset": step.dataset,
                            "action": "CRYPTO_SHRED",
                            "finding": "Subject DEK was not zeroized"
                        })
        finally:
            await pg.close()

        if discrepancies:
            req.state = "PARTIALLY_COMPLETED"
            await LedgerService.append_event(
                session=session,
                tenant_id=tenant_id,
                request_id=request_id,
                event_type="VERIFICATION_FAILED",
                payload={
                    "discrepancies_count": len(discrepancies),
                    "discrepancies": discrepancies
                }
            )
            await session.commit()
            return {
                "verified": False,
                "state": req.state,
                "discrepancies": discrepancies
            }

        req.state = "COMPLETED"

        # Record subject suppression entry (for restore-replay defense)
        suppression_hmac = compute_suppression_hmac(settings.SUPPRESSION_PEPPER, str(req.subject_id))
        supp_entry = SuppressionLedger(
            subject_hmac=suppression_hmac,
            tenant_id=tenant_id,
            request_id=request_id
        )
        session.add(supp_entry)

        # Append to Zero-PII tamper-evident ledger
        await LedgerService.append_event(
            session=session,
            tenant_id=tenant_id,
            request_id=request_id,
            event_type="VERIFICATION_PASSED",
            payload={
                "steps_verified": len(purge_steps),
                "suppression_hmac": suppression_hmac
            }
        )

        await session.commit()
        return {
            "verified": True,
            "state": "COMPLETED",
            "steps_verified": len(purge_steps),
            "suppression_hmac": suppression_hmac
        }
