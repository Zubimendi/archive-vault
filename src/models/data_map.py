import uuid
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from src.database import Base

class DataMapVersion(Base):
    __tablename__ = "data_map_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    document = Column(JSONB, nullable=False)
    status = Column(String(32), nullable=False, default="DRAFT") # DRAFT, ACTIVE, ARCHIVED
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_tenant_version"),
    )
