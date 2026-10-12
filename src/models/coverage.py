import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from src.database import Base

class CoverageFinding(Base):
    __tablename__ = "coverage_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    store_id = Column(String(64), nullable=False)
    object_name = Column(String(255), nullable=False) # e.g. table name
    column_name = Column(String(255), nullable=False)
    finding_type = Column(String(64), nullable=False) # UNMAPPED_COLUMN, UNMAPPED_TABLE, SUSPECT_IDENTIFIER, FK_TO_SUBJECT
    severity = Column(String(16), nullable=False) # LOW, MEDIUM, HIGH, CRITICAL
    detected_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution = Column(String(64), nullable=True) # MAPPED_IN_DATA_MAP, NOT_PERSONAL_DATA, FALSE_POSITIVE
    reviewer = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
