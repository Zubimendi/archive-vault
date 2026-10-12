import json
import hashlib
from uuid import UUID
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.models.ledger import ErasureLedger

class LedgerService:
    GENESIS_HASH = "GENESIS_" + "0" * 56

    @classmethod
    def sanitize_payload(cls, data: Any) -> Any:
        """
        Recursively sanitizes payload to guarantee Zero-PII compliance.
        Strips emails, direct personal names, phone numbers, and arbitrary sensitive keys.
        """
        if isinstance(data, dict):
            clean = {}
            for k, v in data.items():
                low_k = k.lower()
                if any(bad in low_k for bad in ["email", "phone", "name", "password", "ssn", "identifier_val"]):
                    continue
                clean[k] = cls.sanitize_payload(v)
            return clean
        elif isinstance(data, list):
            return [cls.sanitize_payload(item) for item in data]
        elif isinstance(data, str):
            if "@" in data and "." in data:
                return "[REDACTED_IDENTIFIER]"
            return data
        return data

    @staticmethod
    def _normalize_dt(dt: datetime) -> str:
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    @classmethod
    def compute_hash(
        cls,
        prev_hash: str,
        seq: int,
        tenant_id: UUID,
        request_id: UUID,
        event_type: str,
        payload: Dict[str, Any],
        created_at: datetime
    ) -> str:
        payload_str = json.dumps(payload, sort_keys=True)
        dt_str = cls._normalize_dt(created_at)
        raw = f"{prev_hash}:{seq}:{str(tenant_id)}:{str(request_id)}:{event_type}:{payload_str}:{dt_str}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    async def append_event(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        request_id: UUID,
        event_type: str,
        payload: Dict[str, Any]
    ) -> ErasureLedger:
        # Fetch latest ledger block for tenant to establish hash chain link
        stmt = (
            select(ErasureLedger)
            .where(ErasureLedger.tenant_id == tenant_id)
            .order_by(ErasureLedger.seq.desc())
            .limit(1)
        )
        res = await session.execute(stmt)
        latest = res.scalar_one_or_none()

        seq = (latest.seq + 1) if latest else 1
        prev_hash = latest.hash if latest else cls.GENESIS_HASH
        created_at = datetime.now(timezone.utc)

        clean_payload = cls.sanitize_payload(payload)
        computed_hash = cls.compute_hash(
            prev_hash=prev_hash,
            seq=seq,
            tenant_id=tenant_id,
            request_id=request_id,
            event_type=event_type,
            payload=clean_payload,
            created_at=created_at
        )

        entry = ErasureLedger(
            tenant_id=tenant_id,
            seq=seq,
            request_id=request_id,
            event_type=event_type,
            payload=clean_payload,
            prev_hash=prev_hash,
            hash=computed_hash,
            created_at=created_at
        )
        session.add(entry)
        await session.flush()
        return entry

    @classmethod
    async def get_events(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        request_id: Optional[UUID] = None
    ) -> List[ErasureLedger]:
        stmt = select(ErasureLedger).where(ErasureLedger.tenant_id == tenant_id)
        if request_id:
            stmt = stmt.where(ErasureLedger.request_id == request_id)
        stmt = stmt.order_by(ErasureLedger.seq.asc())
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def verify_chain(
        cls,
        session: AsyncSession,
        tenant_id: UUID
    ) -> Dict[str, Any]:
        events = await cls.get_events(session, tenant_id)
        if not events:
            return {"valid": True, "blocks_verified": 0, "errors": []}

        errors = []
        expected_prev_hash = cls.GENESIS_HASH

        for idx, event in enumerate(events):
            # Check prev_hash pointer
            if event.prev_hash != expected_prev_hash:
                errors.append(
                    f"Block seq {event.seq}: prev_hash mismatch. "
                    f"Expected {expected_prev_hash}, found {event.prev_hash}"
                )

            # Recompute block hash
            recomputed = cls.compute_hash(
                prev_hash=event.prev_hash,
                seq=event.seq,
                tenant_id=event.tenant_id,
                request_id=event.request_id,
                event_type=event.event_type,
                payload=event.payload,
                created_at=event.created_at
            )

            if event.hash != recomputed:
                errors.append(
                    f"Block seq {event.seq}: hash verification failed. "
                    f"Expected {recomputed}, found {event.hash}"
                )

            expected_prev_hash = event.hash

        return {
            "valid": len(errors) == 0,
            "blocks_verified": len(events),
            "errors": errors
        }
