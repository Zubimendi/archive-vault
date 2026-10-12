import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from src.database import Base

class ErasureRequest(Base):
    __tablename__ = "erasure_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    subject_id = Column(UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False, index=True)
    trigger = Column(String(32), nullable=False) # SUBJECT_REQUEST, RETENTION_SWEEP, TENANT_OFFBOARD
    regime = Column(String(32), nullable=False)  # GDPR, CCPA, CUSTOM
    data_map_version_id = Column(UUID(as_uuid=True), ForeignKey("data_map_versions.id"), nullable=False)
    received_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    due_at = Column(DateTime(timezone=True), nullable=False)
    cutoff_at = Column(DateTime(timezone=True), nullable=False)
    quarantine_until = Column(DateTime(timezone=True), nullable=True)
    state = Column(String(32), nullable=False, default="RECEIVED") 
    # RECEIVED, PLANNED, QUARANTINED, CANCELLED, HELD, PURGING, PROPAGATING, VERIFYING, COMPLETED, PARTIALLY_COMPLETED, AT_RISK, OVERDUE
    cancellation_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
