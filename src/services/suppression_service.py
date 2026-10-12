from uuid import UUID
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from src.config import settings
from src.models.suppression import SuppressionLedger
from src.crypto.suppression_hmac import compute_suppression_hmac
from src.connectors.postgres import PostgresConnector

class SuppressionService:
    @classmethod
    async def check_suppression(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        identifier: str
    ) -> Dict[str, Any]:
        hmac_digest = compute_suppression_hmac(settings.SUPPRESSION_PEPPER, identifier)
        stmt = select(SuppressionLedger).where(
            SuppressionLedger.tenant_id == tenant_id,
            SuppressionLedger.subject_hmac == hmac_digest
        )
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()

        return {
            "suppressed": record is not None,
            "subject_hmac": hmac_digest,
            "erased_at": record.erased_at.isoformat() if record else None,
            "request_id": str(record.request_id) if record else None
        }

    @classmethod
    async def restore_replay(
        cls,
        session: AsyncSession,
        tenant_id: UUID,
        target_store_id: str,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        # Fetch all suppressed subject HMACs for this tenant
        stmt = select(SuppressionLedger.subject_hmac).where(SuppressionLedger.tenant_id == tenant_id)
        res = await session.execute(stmt)
        suppressed_hmacs = set(res.scalars().all())

        pg = PostgresConnector()
        matches_found = 0
        re_erased = 0
        scanned_tables = ["users", "contacts"]

        try:
            async with (await pg.get_engine()).begin() as conn:
                for table in scanned_tables:
                    # Check if table exists
                    t_check = text("SELECT table_name FROM information_schema.tables WHERE table_name = :t")
                    tr = await conn.execute(t_check, {"t": table})
                    if not tr.scalar():
                        continue

                    # Select rows that have tenant_id and email/identifier
                    fetch_rows = text(f"SELECT id, email FROM {table} WHERE tenant_id = :tenant_id AND email IS NOT NULL")
                    row_res = await conn.execute(fetch_rows, {"tenant_id": tenant_id})
                    rows = row_res.fetchall()

                    for r in rows:
                        row_id, email = r[0], r[1]
                        if not email:
                            continue
                        calculated_hmac = compute_suppression_hmac(settings.SUPPRESSION_PEPPER, email)
                        if calculated_hmac in suppressed_hmacs:
                            matches_found += 1
                            # Re-erase restored row
                            del_stmt = text(f"DELETE FROM {table} WHERE id = :id")
                            await conn.execute(del_stmt, {"id": row_id})
                            re_erased += 1
        finally:
            await pg.close()

        return {
            "target_store_id": target_store_id,
            "scanned_tables": scanned_tables,
            "matches_found": matches_found,
            "re_erased": re_erased,
            "status": "COMPLETED",
            "notes": notes or "Restore-replay sweep executed against target store"
        }
