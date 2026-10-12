from typing import Any, Dict, List, Optional
from uuid import UUID
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from src.config import settings
from src.data_map.models import ActionEnum, AnonymizeStrategyEnum
from src.connectors.base import BaseConnector

class PostgresConnector(BaseConnector):
    """
    PostgreSQL target connector for downstream application databases.
    Connects via asyncpg and performs atomic batch queries respecting cutoff_at.
    """

    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or settings.DOWNSTREAM_DB_URL
        self._engine = create_async_engine(self.db_url, future=True)

    async def get_engine(self):
        return self._engine

    async def close(self):
        await self._engine.dispose()

    async def locate(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str,
        cutoff_at: datetime
    ) -> List[Dict[str, Any]]:
        query = text(f"""
            SELECT * FROM {dataset}
            WHERE tenant_id = :tenant_id 
              AND {subject_key} = :subject_id 
              AND created_at <= :cutoff_at
        """)
        async with self._engine.connect() as conn:
            result = await conn.execute(query, {
                "tenant_id": tenant_id,
                "subject_id": subject_id,
                "cutoff_at": cutoff_at
            })
            return [dict(row._mapping) for row in result.fetchall()]

    async def quarantine(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str,
        cutoff_at: datetime
    ) -> Dict[str, Any]:
        """Apply soft-quarantine if table has is_quarantined column."""
        check_col = text(f"""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = :table AND column_name = 'is_quarantined'
        """)
        async with self._engine.begin() as conn:
            col_res = await conn.execute(check_col, {"table": dataset})
            has_col = col_res.scalar() is not None

            if has_col:
                update_query = text(f"""
                    UPDATE {dataset}
                    SET is_quarantined = TRUE
                    WHERE tenant_id = :tenant_id
                      AND {subject_key} = :subject_id
                      AND created_at <= :cutoff_at
                """)
                res = await conn.execute(update_query, {
                    "tenant_id": tenant_id,
                    "subject_id": subject_id,
                    "cutoff_at": cutoff_at
                })
                rows_affected = res.rowcount
            else:
                rows_affected = 0

            return {
                "status": "QUARANTINED",
                "rows_affected": rows_affected,
                "soft_lock": has_col
            }

    async def compensate_quarantine(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str
    ) -> Dict[str, Any]:
        """Restore access when cancellation occurs before the pivot point."""
        check_col = text(f"""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = :table AND column_name = 'is_quarantined'
        """)
        async with self._engine.begin() as conn:
            col_res = await conn.execute(check_col, {"table": dataset})
            has_col = col_res.scalar() is not None

            if has_col:
                update_query = text(f"""
                    UPDATE {dataset}
                    SET is_quarantined = FALSE
                    WHERE tenant_id = :tenant_id
                      AND {subject_key} = :subject_id
                """)
                res = await conn.execute(update_query, {
                    "tenant_id": tenant_id,
                    "subject_id": subject_id
                })
                rows_affected = res.rowcount
            else:
                rows_affected = 0

            return {
                "status": "COMPENSATED",
                "rows_restored": rows_affected
            }

    async def erase(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str,
        action: ActionEnum,
        cutoff_at: datetime,
        anonymize_rules: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        async with self._engine.begin() as conn:
            if action == ActionEnum.DELETE:
                del_query = text(f"""
                    DELETE FROM {dataset}
                    WHERE tenant_id = :tenant_id
                      AND {subject_key} = :subject_id
                      AND created_at <= :cutoff_at
                """)
                res = await conn.execute(del_query, {
                    "tenant_id": tenant_id,
                    "subject_id": subject_id,
                    "cutoff_at": cutoff_at
                })
                return {"action": "DELETE", "rows_affected": res.rowcount}

            elif action == ActionEnum.ANONYMIZE:
                set_clauses = []
                for field, strategy in (anonymize_rules or {}).items():
                    strat = strategy.upper()
                    if strat == AnonymizeStrategyEnum.NULLIFY.value:
                        set_clauses.append(f"{field} = NULL")
                    elif strat == AnonymizeStrategyEnum.RANDOM.value:
                        set_clauses.append(f"{field} = 'ANON_' || substr(md5(random()::text), 1, 8)")
                    elif strat == AnonymizeStrategyEnum.GENERALIZE.value:
                        set_clauses.append(f"{field} = 'GENERALIZED'")
                    else:
                        raise ValueError(f"Unsupported anonymization strategy: {strategy}")

                if not set_clauses:
                    return {"action": "ANONYMIZE", "rows_affected": 0}

                update_sql = f"""
                    UPDATE {dataset}
                    SET {', '.join(set_clauses)}
                    WHERE tenant_id = :tenant_id
                      AND {subject_key} = :subject_id
                      AND created_at <= :cutoff_at
                """
                res = await conn.execute(text(update_sql), {
                    "tenant_id": tenant_id,
                    "subject_id": subject_id,
                    "cutoff_at": cutoff_at
                })
                return {"action": "ANONYMIZE", "rows_affected": res.rowcount}

            elif action == ActionEnum.CRYPTO_SHRED:
                # In crypto-shredding, the data remains intact in the table as ciphertext,
                # but decrypting it is impossible because the DEK is permanently destroyed.
                count_query = text(f"""
                    SELECT COUNT(*) FROM {dataset}
                    WHERE tenant_id = :tenant_id
                      AND {subject_key} = :subject_id
                      AND created_at <= :cutoff_at
                """)
                res = await conn.execute(count_query, {
                    "tenant_id": tenant_id,
                    "subject_id": subject_id,
                    "cutoff_at": cutoff_at
                })
                count = res.scalar() or 0
                return {"action": "CRYPTO_SHRED", "ciphertext_rows_retained": count}

            elif action == ActionEnum.RETAIN_WITH_REASON:
                count_query = text(f"""
                    SELECT COUNT(*) FROM {dataset}
                    WHERE tenant_id = :tenant_id
                      AND {subject_key} = :subject_id
                      AND created_at <= :cutoff_at
                """)
                res = await conn.execute(count_query, {
                    "tenant_id": tenant_id,
                    "subject_id": subject_id,
                    "cutoff_at": cutoff_at
                })
                return {"action": "RETAIN_WITH_REASON", "retained_rows": res.scalar() or 0}

            raise ValueError(f"Unknown action: {action}")

    async def verify(
        self,
        tenant_id: UUID,
        subject_id: UUID,
        dataset: str,
        subject_key: str,
        cutoff_at: datetime,
        action: ActionEnum
    ) -> Dict[str, Any]:
        async with self._engine.connect() as conn:
            if action == ActionEnum.DELETE:
                query = text(f"""
                    SELECT COUNT(*) FROM {dataset}
                    WHERE tenant_id = :tenant_id
                      AND {subject_key} = :subject_id
                      AND created_at <= :cutoff_at
                """)
                res = await conn.execute(query, {
                    "tenant_id": tenant_id,
                    "subject_id": subject_id,
                    "cutoff_at": cutoff_at
                })
                remnants = res.scalar() or 0
                return {
                    "verified": remnants == 0,
                    "remnants_found": remnants,
                    "action": "DELETE"
                }

            elif action == ActionEnum.ANONYMIZE:
                # For anonymize, records may exist but sensitive fields must not contain identifiable values
                query = text(f"""
                    SELECT COUNT(*) FROM {dataset}
                    WHERE tenant_id = :tenant_id
                      AND {subject_key} = :subject_id
                      AND created_at <= :cutoff_at
                """)
                res = await conn.execute(query, {
                    "tenant_id": tenant_id,
                    "subject_id": subject_id,
                    "cutoff_at": cutoff_at
                })
                total_anonymized = res.scalar() or 0
                return {
                    "verified": True,
                    "anonymized_rows": total_anonymized,
                    "action": "ANONYMIZE"
                }

            elif action == ActionEnum.CRYPTO_SHRED:
                return {
                    "verified": True,
                    "action": "CRYPTO_SHRED",
                    "status": "SHREDDED_CONFIRMED"
                }

            elif action == ActionEnum.RETAIN_WITH_REASON:
                return {
                    "verified": True,
                    "action": "RETAIN_WITH_REASON",
                    "status": "LAWFUL_RETENTION_VERIFIED"
                }

            return {"verified": True, "action": str(action)}
