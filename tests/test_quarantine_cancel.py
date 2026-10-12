import pytest
import uuid
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from src.connectors.postgres import PostgresConnector
from src.services.tenant_service import TenantService
from src.services.subject_service import SubjectService
from src.services.data_map_service import DataMapService
from src.services.planner_service import PlannerService
from src.services.saga_service import SagaService

@pytest.mark.asyncio
async def test_quarantine_compensation_and_pivot_irreversibility(db_session: AsyncSession):
    """
    Claim 3 (Quarantine Compensation & Irreversibility):
    - Cancelling pre-pivot reverts quarantine and restores data access.
    - Attempting cancellation post-pivot is rejected (point of no return).
    """
    tenant = await TenantService.create_tenant(db_session, "Quarantine Corp")
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

    subject = await SubjectService.register_subject(
        db_session, tenant.id, "EMAIL", "quarantine_user@test.com"
    )

    pg = PostgresConnector()
    engine = await pg.get_engine()

    # Seed downstream row
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO users (id, tenant_id, email, full_name, is_quarantined, created_at)
            VALUES (:id, :tenant_id, 'quarantine_user@test.com', 'Quarantine Test', FALSE, :now)
        """), {"id": subject.id, "tenant_id": tenant.id, "now": datetime.utcnow()})

    # 1. Create request with 3-day quarantine window
    req = await PlannerService.create_erasure_request(
        db_session, tenant.id, subject.id, quarantine_days=3
    )
    assert req.state == "QUARANTINED"

    # Execute quarantine step
    step_res = await SagaService.execute_next_step(db_session, tenant.id, req.id)
    assert step_res["phase"] == "QUARANTINE"

    # Verify is_quarantined is TRUE
    async with engine.connect() as conn:
        is_q = (await conn.execute(text("SELECT is_quarantined FROM users WHERE id = :id"), {"id": subject.id})).scalar()
        assert is_q is True

    # 2. Pre-pivot cancellation: should compensate quarantine
    cancel_res = await SagaService.cancel_request(
        session=db_session,
        tenant_id=tenant.id,
        request_id=req.id,
        cancellation_reason="Forged request verification",
        actor="security-ops"
    )
    assert cancel_res["state"] == "CANCELLED"
    assert cancel_res["compensated_steps"] >= 1

    # Verify access restored (is_quarantined back to FALSE)
    async with engine.connect() as conn:
        is_q = (await conn.execute(text("SELECT is_quarantined FROM users WHERE id = :id"), {"id": subject.id})).scalar()
        assert is_q is False

    # 3. Post-pivot cancellation rejection test
    # Create new subject and request
    subject2 = await SubjectService.register_subject(
        db_session, tenant.id, "EMAIL", "pivot_user@test.com"
    )
    req2 = await PlannerService.create_erasure_request(
        db_session, tenant.id, subject2.id, quarantine_days=3
    )

    # Force pivot to purge
    pivot_res = await SagaService.pivot_to_purge(
        session=db_session,
        tenant_id=tenant.id,
        request_id=req2.id,
        bypass_quarantine_window=True
    )
    assert pivot_res["state"] == "PURGING"

    # Attempt cancel after pivot -> Must raise ValueError
    with pytest.raises(ValueError, match="Cannot cancel erasure request in 'PURGING' state"):
        await SagaService.cancel_request(
            session=db_session,
            tenant_id=tenant.id,
            request_id=req2.id,
            cancellation_reason="Late user regret"
        )

    await pg.close()
