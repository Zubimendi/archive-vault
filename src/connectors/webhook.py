import hmac
import hashlib
import json
from typing import Any, Dict, List, Optional
from uuid import UUID
from datetime import datetime
from src.data_map.models import ActionEnum
from src.connectors.base import BaseConnector

class WebhookConnector(BaseConnector):
    """
    Webhook connector for third-party processors.
    Generates HMAC-signed deletion notifications and simulates vendor delivery.
    """

    def __init__(self, signing_secret: str = "webhook_secret_key_12345"):
        self.signing_secret = signing_secret

    async def locate(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str,
        cutoff_at: datetime
    ) -> List[Dict[str, Any]]:
        # Remote vendor store assumed to hold data corresponding to subject
        return [{"dataset": dataset, "subject_id": str(subject_id), "status": "REMOTE_TARGET"}]

    async def quarantine(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str,
        cutoff_at: datetime
    ) -> Dict[str, Any]:
        return {"status": "QUARANTINED", "vendor": dataset, "soft_lock": True}

    async def compensate_quarantine(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str
    ) -> Dict[str, Any]:
        return {"status": "COMPENSATED", "vendor": dataset}

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
        # Formulate non-PII signed dispatch envelope
        payload = {
            "tenant_id": str(tenant_id),
            "subject_id": str(subject_id),
            "action": action.value,
            "cutoff_at": cutoff_at.isoformat(),
            "timestamp": datetime.utcnow().isoformat()
        }
        raw = json.dumps(payload, sort_keys=True)
        sig = hmac.new(self.signing_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()

        return {
            "action": action.value,
            "status": "DISPATCHED",
            "vendor_target": dataset,
            "signature": sig,
            "status_code": 200
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
            "vendor_target": dataset,
            "receipt_code": "ACK_200_VENDOR_CONFIRMED"
        }
