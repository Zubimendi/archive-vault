from uuid import UUID
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class TenantCreate(BaseModel):
    name: str
    regime_profile: str = "GDPR"
    kek_ref: Optional[str] = None

class TenantOffboard(BaseModel):
    confirmation: str
    reason: str

class DataMapUpload(BaseModel):
    version: int
    document: Dict[str, Any]

class SubjectRegister(BaseModel):
    identifier_type: str = "EMAIL"
    identifier_value: str
    pepper_context: Optional[str] = None

class ErasureRequestCreate(BaseModel):
    subject_id: UUID
    trigger: str = "SUBJECT_REQUEST"
    regime: str = "GDPR"
    quarantine_days: int = 3

class ErasureRequestCancel(BaseModel):
    cancellation_reason: str
    actor: Optional[str] = None

class ErasureRequestPivot(BaseModel):
    bypass_quarantine_window: bool = False
    reason: Optional[str] = None

class ErasureRequestExtend(BaseModel):
    extension_days: int
    legal_basis: str
    subject_notified: bool
    authorized_by: str

class LegalHoldCreate(BaseModel):
    subject_id: UUID
    reason: str
    placed_by: str
    expires_in_days: Optional[int] = None

class LegalHoldRelease(BaseModel):
    released_by: str
    release_reason: str

class SuppressionCheck(BaseModel):
    identifier: str

class RestoreReplay(BaseModel):
    target_store_id: str
    notes: Optional[str] = None

class CoverageScan(BaseModel):
    store_id: str

class CoverageResolve(BaseModel):
    resolution: str
    reviewer: str
    notes: Optional[str] = None
