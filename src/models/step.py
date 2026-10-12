import uuid
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from src.database import Base

class ErasureStep(Base):
    __tablename__ = "erasure_steps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(UUID(as_uuid=True), ForeignKey("erasure_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    store_id = Column(String(64), nullable=False)
    dataset = Column(String(64), nullable=False)
    phase = Column(String(32), nullable=False) # QUARANTINE, PURGE, PROPAGATE
    action = Column(String(32), nullable=False) # DELETE, ANONYMIZE, CRYPTO_SHRED, RETAIN_WITH_REASON
    state = Column(String(32), nullable=False, default="PENDING") # PENDING, IN_PROGRESS, COMPLETED, FAILED, COMPENSATING, CANCELLED
    attempts = Column(Integer, nullable=False, default=0)
    evidence = Column(JSONB, nullable=True) # Non-PII verification metadata (e.g. rows affected, digest)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
