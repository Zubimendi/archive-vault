from uuid import UUID
from datetime import datetime
from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from src.models.data_map import DataMapVersion
from src.data_map.validator import DataMapValidator

class DataMapService:
    @staticmethod
    async def upload_data_map(
        session: AsyncSession,
        tenant_id: UUID,
        version: int,
        document: Dict[str, Any]
    ) -> DataMapVersion:
        # Pydantic validation: validates stores, datasets, actions, and rejects forbidden HASH anonymization
        validated_doc = DataMapValidator.parse_and_validate(document)

        dm_version = DataMapVersion(
            tenant_id=tenant_id,
            version=version,
            document=validated_doc.model_dump(),
            status="DRAFT"
        )
        session.add(dm_version)
        await session.commit()
        await session.refresh(dm_version)
        return dm_version

    @staticmethod
    async def publish_data_map(
        session: AsyncSession,
        tenant_id: UUID,
        version_id: UUID
    ) -> DataMapVersion:
        stmt = select(DataMapVersion).where(
            DataMapVersion.tenant_id == tenant_id,
            DataMapVersion.id == version_id
        )
        res = await session.execute(stmt)
        target = res.scalar_one_or_none()
        if not target:
            raise ValueError(f"Data map version {version_id} not found for tenant")

        # Archive current active version if any
        archive_stmt = (
            update(DataMapVersion)
            .where(
                DataMapVersion.tenant_id == tenant_id,
                DataMapVersion.status == "ACTIVE"
            )
            .values(status="ARCHIVED")
        )
        await session.execute(archive_stmt)

        target.status = "ACTIVE"
        target.published_at = datetime.utcnow()
        await session.commit()
        await session.refresh(target)
        return target

    @staticmethod
    async def get_active_data_map(
        session: AsyncSession,
        tenant_id: UUID
    ) -> Optional[DataMapVersion]:
        stmt = select(DataMapVersion).where(
            DataMapVersion.tenant_id == tenant_id,
            DataMapVersion.status == "ACTIVE"
        ).order_by(DataMapVersion.version.desc()).limit(1)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()
