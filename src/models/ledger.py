import uuid
from datetime import datetime
from sqlalchemy import Column, BigInteger, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from src.database import Base

class ErasureLedger(Base):
    __tablename__ = "erasure_ledger"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    seq = Column(BigInteger, nullable=False)
    request_id = Column(UUID(as_uuid=True), ForeignKey("erasure_requests.id", ondelete="RESTRICT"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False)
    payload = Column(JSONB, nullable=False) # STRICTLY NO PII: only counts, dataset names, step outcomes
    prev_hash = Column(String(64), nullable=False)
    hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "seq", name="uq_ledger_tenant_seq"),
    )
