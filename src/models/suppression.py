from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from src.database import Base

class SuppressionLedger(Base):
    __tablename__ = "suppression_ledger"

    # HMAC-SHA256 with pepper; NEVER plaintext or plain hash
    subject_hmac = Column(String(128), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    request_id = Column(UUID(as_uuid=True), ForeignKey("erasure_requests.id", ondelete="CASCADE"), nullable=False)
    erased_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
