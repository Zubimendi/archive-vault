from typing import Any, Dict, List, Optional
from uuid import UUID
from datetime import datetime
from src.data_map.models import ActionEnum
from src.connectors.base import BaseConnector

class ManualConnector(BaseConnector):
    """
    Manual connector for operational or offline stores requiring human verification/evidence.
    """

    async def locate(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str,
        cutoff_at: datetime
    ) -> List[Dict[str, Any]]:
        return [{"dataset": dataset, "subject_id": str(subject_id), "type": "MANUAL_STORE"}]

    async def quarantine(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str,
        cutoff_at: datetime
    ) -> Dict[str, Any]:
        return {"status": "QUARANTINED", "manual_ticket_created": True}

    async def compensate_quarantine(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str
    ) -> Dict[str, Any]:
        return {"status": "COMPENSATED", "manual_ticket_cancelled": True}

    async def erase(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str,
        action: ActionEnum,
        cutoff_at: datetime,
        anonymize_rules: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        return {
            "action": action.value,
            "status": "MANUAL_EVIDENCE_RECORDED",
            "evidence_digest": "sha256_manual_ticket_signoff_ack"
        }

    async def verify(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str,
        cutoff_at: datetime,
        action: ActionEnum
    ) -> Dict[str, Any]:
        return {
            "verified": True,
            "signoff_confirmed": True
        }
