import re
from uuid import UUID
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from src.models.coverage import CoverageFinding
from src.models.data_map import DataMapVersion
from src.connectors.postgres import PostgresConnector

class CoverageService:
    SUSPECT_PII_REGEX = re.compile(r"(?i)(email|ssn|phone|password|secret|dob|first_name|last_name|address|user)")

    @classmethod
    async def scan_drift(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        store_id: str
    ) -> Dict[str, Any]:
        # 1. Fetch active data map
        dm_stmt = select(DataMapVersion).where(
            DataMapVersion.tenant_id == tenant_id,
            DataMapVersion.status == "ACTIVE"
        ).order_by(DataMapVersion.version.desc()).limit(1)
        dm_res = await session.execute(dm_stmt)
        active_dm = dm_res.scalar_one_or_none()

        mapped_tables = set()
        if active_dm and active_dm.document:
            for store in active_dm.document.get("stores", []):
                for ds in store.get("datasets", []):
                    mapped_tables.add(ds.get("name"))

        # 2. Introspect downstream database tables & columns
        pg = PostgresConnector()
        new_findings = 0
        try:
            async with (await pg.get_engine()).connect() as conn:
                q = text("""
                    SELECT table_name, column_name 
                    FROM information_schema.columns 
                    WHERE table_schema = 'public'
                """)
                res = await conn.execute(q)
                columns = res.fetchall()

                for tbl, col in columns:
                    if tbl not in mapped_tables:
                        # Unmapped table / column
                        is_suspect = bool(cls.SUSPECT_PII_REGEX.search(col))
                        severity = "CRITICAL" if is_suspect else "HIGH"
                        finding_type = "SUSPECT_IDENTIFIER" if is_suspect else "UNMAPPED_TABLE"

                        # Check if finding already exists
                        exist_stmt = select(CoverageFinding).where(
                            CoverageFinding.tenant_id == tenant_id,
                            CoverageFinding.store_id == store_id,
                            CoverageFinding.object_name == tbl,
                            CoverageFinding.column_name == col
                        )
                        ex = (await session.execute(exist_stmt)).scalar_one_or_none()
                        if not ex:
                            finding = CoverageFinding(
                                tenant_id=tenant_id,
                                store_id=store_id,
                                object_name=tbl,
                                column_name=col,
                                finding_type=finding_type,
                                severity=severity,
                                detected_at=datetime.utcnow()
                            )
                            session.add(finding)
                            new_findings += 1
        finally:
            await pg.close()

        await session.commit()
        return {
            "store_id": store_id,
            "new_findings_detected": new_findings,
            "status": "COMPLETED",
            "message": f"Schema drift introspection completed. Found {new_findings} new findings."
        }

    @classmethod
    async def get_findings(
        cls,
        session: AsyncSession,
        tenant_id: UUID
    ) -> List[CoverageFinding]:
        stmt = (
            select(CoverageFinding)
            .where(CoverageFinding.tenant_id == tenant_id)
            .order_by(CoverageFinding.detected_at.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def resolve_finding(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        finding_id: UUID,
        resolution: str,
        reviewer: str,
        notes: Optional[str] = None
    ) -> CoverageFinding:
        stmt = select(CoverageFinding).where(
            CoverageFinding.tenant_id == tenant_id,
            CoverageFinding.id == finding_id
        )
        res = await session.execute(stmt)
        finding = res.scalar_one_or_none()
        if not finding:
            raise ValueError(f"Coverage finding {finding_id} not found")

        finding.resolution = resolution
        finding.reviewer = reviewer
        finding.notes = notes
        finding.resolved_at = datetime.utcnow()

        await session.commit()
        await session.refresh(finding)
        return finding

    @classmethod
    async def get_health(
        cls,
        session: AsyncSession,
        tenant_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        stmt = select(CoverageFinding).where(CoverageFinding.resolved_at.is_(None))
        if tenant_id:
            stmt = stmt.where(CoverageFinding.tenant_id == tenant_id)
        res = await session.execute(stmt)
        unresolved = list(res.scalars().all())

        crit = sum(1 for f in unresolved if f.severity == "CRITICAL")
        high = sum(1 for f in unresolved if f.severity == "HIGH")

        return {
            "compliant": (crit == 0 and high == 0),
            "unresolved_critical": crit,
            "unresolved_high": high,
            "total_unresolved": len(unresolved)
        }
