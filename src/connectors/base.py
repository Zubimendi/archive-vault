from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from uuid import UUID
from datetime import datetime
from src.data_map.models import ActionEnum

class BaseConnector(ABC):
    """
    Abstract connector interface for target data stores.
    Defines the contract for locate, quarantine, erase, and verify.
    """

    @abstractmethod
    async def locate(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str,
        cutoff_at: datetime
    ) -> List[Dict[str, Any]]:
        """Locate subject records created at or before cutoff_at."""
        pass

    @abstractmethod
    async def quarantine(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str,
        cutoff_at: datetime
    ) -> Dict[str, Any]:
        """Apply soft-quarantine / access restriction on subject data."""
        pass

    @abstractmethod
    async def compensate_quarantine(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str
    ) -> Dict[str, Any]:
        """Revert quarantine if cancellation is requested prior to the pivot."""
        pass

    @abstractmethod
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
        """Execute erasure action (DELETE, ANONYMIZE, CRYPTO_SHRED, RETAIN_WITH_REASON)."""
        pass

    @abstractmethod
    async def verify(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str,
        cutoff_at: datetime,
        action: ActionEnum
    ) -> Dict[str, Any]:
        """Independently verify that erasure action was successful and no remnants exist."""
        pass
