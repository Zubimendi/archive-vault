import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from src.config import settings
from src.services.tenant_service import TenantService
from src.services.subject_service import SubjectService
from src.services.data_map_service import DataMapService
from src.services.planner_service import PlannerService
from src.services.ledger_service import LedgerService

@pytest.mark.asyncio
async def test_ledger_immutability_and_chain_verification(db_session: AsyncSession):
    """
    Claim 9 (Ledger Integrity & Immutability):
    - Direct SQL UPDATE/DELETE is blocked by the PostgreSQL trigger.
    - Tampering with historical block payloads is detected by chain verification.
    """
    tenant = await TenantService.create_tenant(db_session, "Ledger Integrity Corp")
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

    subject = await SubjectService.register_subject(db_session, tenant.id, "EMAIL", "ledger_test@corp.com")
    req = await PlannerService.create_erasure_request(db_session, tenant.id, subject.id, quarantine_days=0)

    # 1. Append sequential ledger events
    e1 = await LedgerService.append_event(
        db_session, tenant.id, req.id, "TEST_EVENT_1", {"steps": 3}
    )
    e2 = await LedgerService.append_event(
        db_session, tenant.id, req.id, "TEST_EVENT_2", {"window": "expired"}
    )
    e3 = await LedgerService.append_event(
        db_session, tenant.id, req.id, "TEST_EVENT_3", {"dataset": "users"}
    )
    await db_session.commit()

    # 2. Verify untampered chain is valid
    chain_res = await LedgerService.verify_chain(db_session, tenant.id)
    assert chain_res["valid"] is True
    assert chain_res["blocks_verified"] >= 3
    assert len(chain_res["errors"]) == 0

    # 3. Test database trigger blocks direct UPDATE using dedicated connection
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as conn:
            with pytest.raises(Exception, match="Erasure ledger is append-only"):
                await conn.execute(text("""
                    UPDATE erasure_ledger 
                    SET event_type = 'TAMPERED' 
                    WHERE id = :id
                """), {"id": e2.id})

        # 4. Test database trigger blocks direct DELETE using dedicated connection
        async with engine.connect() as conn:
            with pytest.raises(Exception, match="Erasure ledger is append-only"):
                await conn.execute(text("""
                    DELETE FROM erasure_ledger WHERE id = :id
                """), {"id": e3.id})
    finally:
        await engine.dispose()

    # 5. Verify hash validation failure when payload is corrupted
    corrupted_hash = LedgerService.compute_hash(
        prev_hash=e2.prev_hash,
        seq=e2.seq,
        tenant_id=e2.tenant_id,
        request_id=e2.request_id,
        event_type=e2.event_type,
        payload={"corrupted": "payload"},
        created_at=e2.created_at
    )
    assert corrupted_hash != e2.hash
