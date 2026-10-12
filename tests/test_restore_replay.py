import pytest
import uuid
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from src.config import settings
from src.crypto.suppression_hmac import compute_suppression_hmac
from src.models.suppression import SuppressionLedger
from src.services.tenant_service import TenantService
from src.services.subject_service import SubjectService
from src.services.data_map_service import DataMapService
from src.services.planner_service import PlannerService
from src.services.suppression_service import SuppressionService
from src.connectors.postgres import PostgresConnector

@pytest.mark.asyncio
async def test_restore_replay_re_erasure(db_session: AsyncSession):
    """
    Claim 7 (Restore-Replay Defense):
    When a database is restored from an older backup snapshot, any reappearing
    records matching the suppression ledger are automatically detected and re-erased.
    """
    tenant = await TenantService.create_tenant(db_session, "Restore Replay Corp")
    doc = {
        "stores": [
            {
                "id": "app-postgres",
                "connector": "postgres",
                "datasets": [
                    {"name": "users", "subject_key": "id", "action": "DELETE"}
                ]
            }
        ]
    }
    dm = await DataMapService.upload_data_map(db_session, tenant.id, 1, doc)
    await DataMapService.publish_data_map(db_session, tenant.id, dm.id)

    email = f"zombie_{uuid.uuid4().hex[:8]}@enterprise.io"
    subject = await SubjectService.register_subject(db_session, tenant.id, "EMAIL", email)
    req = await PlannerService.create_erasure_request(db_session, tenant.id, subject.id, quarantine_days=0)

    # 1. Subject was previously erased; suppression HMAC is logged
    hmac_digest = compute_suppression_hmac(settings.SUPPRESSION_PEPPER, email)
    supp_entry = SuppressionLedger(
        subject_hmac=hmac_digest,
        tenant_id=tenant.id,
        request_id=req.id
    )
    db_session.add(supp_entry)
    await db_session.commit()

    # 2. Simulate restoring an older backup snapshot into downstream users table
    restored_row_id = uuid.uuid4()
    pg = PostgresConnector()
    engine = await pg.get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO users (id, tenant_id, email, full_name, created_at)
            VALUES (:id, :tenant_id, :email, 'Restored Zombie', :now)
        """), {
            "id": restored_row_id,
            "tenant_id": tenant.id,
            "email": email,
            "now": datetime.utcnow()
        })

    # Verify restored record exists in DB before replay
    async with engine.connect() as conn:
        r = (await conn.execute(text("SELECT id FROM users WHERE id = :id"), {"id": restored_row_id})).scalar()
        assert r == restored_row_id

    # 3. Trigger restore-replay background worker scan
    res = await SuppressionService.restore_replay(
        session=db_session,
        tenant_id=tenant.id,
        target_store_id="app-postgres",
        notes="Testing restored snapshot reconciliation"
    )

    assert res["matches_found"] >= 1
    assert res["re_erased"] >= 1

    # 4. Verify restored record has been purged
    async with engine.connect() as conn:
        r_after = (await conn.execute(text("SELECT id FROM users WHERE id = :id"), {"id": restored_row_id})).scalar()
        assert r_after is None

    await pg.close()
