import uuid
from datetime import datetime
from sqlalchemy import Column, String, LargeBinary, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from src.database import Base

class Subject(Base):
    __tablename__ = "subjects"

    # Opaque internal UUID - NEVER stores raw email, name, phone, etc.
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    wrapped_dek = Column(LargeBinary, nullable=False)
    dek_iv = Column(LargeBinary, nullable=False)
    status = Column(String(32), nullable=False, default="ACTIVE") # ACTIVE, QUARANTINED, ERASED
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_subject"),
    )
