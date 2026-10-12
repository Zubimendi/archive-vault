from uuid import UUID
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.config import settings
from src.models.request import ErasureRequest
from src.models.step import ErasureStep
from src.models.subject import Subject
from src.models.data_map import DataMapVersion
from src.data_map.models import ActionEnum, ConnectorTypeEnum
from src.services.ledger_service import LedgerService

class PlannerService:
    TERMINAL_STATES = {"COMPLETED", "CANCELLED", "PARTIALLY_COMPLETED"}

    @classmethod
    async def create_erasure_request(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        subject_id: UUID,
        trigger: str = "SUBJECT_REQUEST",
        regime: str = "GDPR",
        quarantine_days: int = 3
    ) -> ErasureRequest:
        # Deduplication check: reject in-flight request for same subject
        dup_stmt = select(ErasureRequest).where(
            ErasureRequest.tenant_id == tenant_id,
            ErasureRequest.subject_id == subject_id,
            ErasureRequest.state.not_in(cls.TERMINAL_STATES)
        )
        res = await session.execute(dup_stmt)
        existing = res.scalar_one_or_none()
        if existing:
            raise ValueError(f"Active erasure request already in-flight for subject {subject_id}")

        # Check subject existence
        subj_stmt = select(Subject).where(Subject.tenant_id == tenant_id, Subject.id == subject_id)
        sres = await session.execute(subj_stmt)
        subject = sres.scalar_one_or_none()
        if not subject:
            raise ValueError(f"Subject {subject_id} not found for tenant")

        # Fetch active Data Map version
        dm_stmt = select(DataMapVersion).where(
            DataMapVersion.tenant_id == tenant_id,
            DataMapVersion.status == "ACTIVE"
        ).order_by(DataMapVersion.version.desc()).limit(1)
        dm_res = await session.execute(dm_stmt)
        data_map = dm_res.scalar_one_or_none()
        if not data_map:
            raise ValueError("No active Data Map published for this tenant. Cannot plan erasure.")

        now = datetime.utcnow()
        cutoff_at = now  # Frozen snapshot timestamp to prevent race with newly created rows

        # Calculate legal deadline
        regime_upper = regime.upper()
        if regime_upper == "CCPA":
            due_at = now + timedelta(days=settings.CCPA_DEADLINE_DAYS)
        else:
            due_at = now + timedelta(days=settings.GDPR_DEADLINE_DAYS)

        quarantine_until = (now + timedelta(days=quarantine_days)) if quarantine_days > 0 else now
        initial_state = "QUARANTINED" if quarantine_days > 0 else "PLANNED"

        erasure_request = ErasureRequest(
            tenant_id=tenant_id,
            subject_id=subject_id,
            trigger=trigger,
            regime=regime_upper,
            data_map_version_id=data_map.id,
            received_at=now,
            due_at=due_at,
            cutoff_at=cutoff_at,
            quarantine_until=quarantine_until,
            state=initial_state
        )
        session.add(erasure_request)
        await session.flush()

        # Expand Data Map into ordered saga steps
        steps = []
        doc = data_map.document
        stores = doc.get("stores", [])

        # Phase 1: QUARANTINE steps
        if quarantine_days > 0:
            for store in stores:
                store_id = store.get("id")
                connector = store.get("connector")
                if connector == ConnectorTypeEnum.POSTGRES.value:
                    for ds in store.get("datasets", []):
                        steps.append(ErasureStep(
                            request_id=erasure_request.id,
                            store_id=store_id,
                            dataset=ds.get("name"),
                            phase="QUARANTINE",
                            action="QUARANTINE",
                            state="PENDING",
                            evidence={"subject_key": ds.get("subject_key")}
                        ))

        # Phase 2: PURGE steps (DELETE, ANONYMIZE, CRYPTO_SHRED, RETAIN_WITH_REASON)
        for store in stores:
            store_id = store.get("id")
            connector = store.get("connector")
            if connector == ConnectorTypeEnum.POSTGRES.value:
                for ds in store.get("datasets", []):
                    action_val = ds.get("action")
                    evidence_meta = {
                        "subject_key": ds.get("subject_key"),
                        "anonymize_rules": ds.get("anonymize"),
                        "retention": ds.get("retention")
                    }
                    steps.append(ErasureStep(
                        request_id=erasure_request.id,
                        store_id=store_id,
                        dataset=ds.get("name"),
                        phase="PURGE",
                        action=action_val,
                        state="PENDING",
                        evidence=evidence_meta
                    ))

        # Phase 3: PROPAGATE steps (webhook, manual)
        for store in stores:
            store_id = store.get("id")
            connector = store.get("connector")
            if connector in (ConnectorTypeEnum.WEBHOOK.value, ConnectorTypeEnum.MANUAL.value):
                for ds in store.get("datasets", []):
                    steps.append(ErasureStep(
                        request_id=erasure_request.id,
                        store_id=store_id,
                        dataset=ds.get("name"),
                        phase="PROPAGATE",
                        action=ds.get("action", "DELETE"),
                        state="PENDING",
                        evidence={"connector": connector, "subject_key": ds.get("subject_key")}
                    ))

        for step in steps:
            session.add(step)
        await session.flush()

        # Append Zero-PII event to append-only tamper-evident ledger
        await LedgerService.append_event(
            session=session,
            tenant_id=tenant_id,
            request_id=erasure_request.id,
            event_type="REQUEST_RECEIVED",
            payload={
                "trigger": trigger,
                "regime": regime_upper,
                "quarantine_days": quarantine_days,
                "total_steps": len(steps),
                "data_map_version": data_map.version
            }
        )

        await session.commit()
        await session.refresh(erasure_request)
        return erasure_request

    @classmethod
    async def get_request(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        request_id: UUID
    ) -> Optional[Dict[str, Any]]:
        stmt = select(ErasureRequest).where(
            ErasureRequest.tenant_id == tenant_id,
            ErasureRequest.id == request_id
        )
        res = await session.execute(stmt)
        req = res.scalar_one_or_none()
        if not req:
            return None

        step_stmt = (
            select(ErasureStep)
            .where(ErasureStep.request_id == request_id)
            .order_by(ErasureStep.created_at.asc())
        )
        s_res = await session.execute(step_stmt)
        steps = list(s_res.scalars().all())

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
            "cancellation_reason": req.cancellation_reason,
            "steps": [
                {
                    "id": str(s.id),
                    "store_id": s.store_id,
                    "dataset": s.dataset,
                    "phase": s.phase,
                    "action": s.action,
                    "state": s.state,
                    "attempts": s.attempts,
                    "evidence": s.evidence,
                    "last_error": s.last_error
                }
                for s in steps
            ]
        }
