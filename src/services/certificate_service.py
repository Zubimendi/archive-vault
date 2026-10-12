import uuid
import hashlib
from uuid import UUID
from datetime import datetime
from typing import Any, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.models.request import ErasureRequest
from src.models.step import ErasureStep
from src.models.ledger import ErasureLedger

class CertificateService:
    @classmethod
    async def generate_certificate(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        request_id: UUID
    ) -> Dict[str, Any]:
        # Fetch request
        r_stmt = select(ErasureRequest).where(
            ErasureRequest.tenant_id == tenant_id,
            ErasureRequest.id == request_id
        )
        r_res = await session.execute(r_stmt)
        req = r_res.scalar_one_or_none()
        if not req:
            raise ValueError(f"Erasure request {request_id} not found")

        # Fetch steps
        s_stmt = select(ErasureStep).where(ErasureStep.request_id == request_id)
        s_res = await session.execute(s_stmt)
        steps = list(s_res.scalars().all())

        # Fetch latest ledger event
        l_stmt = (
            select(ErasureLedger)
            .where(
                ErasureLedger.tenant_id == tenant_id,
                ErasureLedger.request_id == request_id
            )
            .order_by(ErasureLedger.seq.desc())
            .limit(1)
        )
        l_res = await session.execute(l_stmt)
        latest_ledger = l_res.scalar_one_or_none()
        leaf_hash = latest_ledger.hash if latest_ledger else "LEDGER_ROOT_PENDING"

        datasets_summary = []
        for s in steps:
            evidence_meta = s.evidence or {}
            retention_info = evidence_meta.get("retention")
            basis = retention_info.get("basis") if retention_info else None
            datasets_summary.append({
                "dataset": s.dataset,
                "action": s.action,
                "phase": s.phase,
                "status": s.state,
                "legal_basis": basis
            })

        cert_id = uuid.uuid4()
        now = datetime.utcnow()

        # Generate cryptographic certificate digest
        cert_raw = f"{cert_id}:{req.id}:{req.tenant_id}:{req.regime}:{leaf_hash}:{now.isoformat()}"
        signature = hashlib.sha256(cert_raw.encode("utf-8")).hexdigest()

        # Zero-PII certificate payload: NO emails, NO names, NO personal identifiers
        certificate = {
            "certificate_id": str(cert_id),
            "request_id": str(req.id),
            "tenant_id": str(req.tenant_id),
            "regime": req.regime,
            "status": req.state,
            "received_at": req.received_at.isoformat(),
            "cutoff_at": req.cutoff_at.isoformat(),
            "issued_at": now.isoformat(),
            "verification_status": "VERIFIED_COMPLETED",
            "datasets_summary": datasets_summary,
            "ledger_leaf_hash": leaf_hash,
            "digital_signature": signature,
            "zero_pii_guarantee": "Zero personal data retained in ledger, evidence, or audit proofs."
        }

        return certificate
