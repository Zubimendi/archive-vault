import pytest
import uuid
from datetime import datetime, timedelta
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from src.connectors.postgres import PostgresConnector
from src.services.tenant_service import TenantService
from src.services.subject_service import SubjectService
from src.services.data_map_service import DataMapService
from src.services.planner_service import PlannerService
from src.services.saga_service import SagaService

@pytest.mark.asyncio
async def test_subject_recreation_race(db_session: AsyncSession):
    """
    Claim 4 (Subject-Recreation Race):
    Starting erasure on subject 1, then recreating a new account with the same email
    mid-saga after cutoff_at must leave the new account's data completely intact.
    """
    tenant = await TenantService.create_tenant(db_session, "Race Condition Corp")
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

    email = "alice_race@enterprise.io"
    t0 = datetime.utcnow() - timedelta(minutes=10)

    # 1. Subject 1 registered and row inserted in downstream users table
    subject_1 = await SubjectService.register_subject(db_session, tenant.id, "EMAIL", email)

    pg = PostgresConnector()
    engine = await pg.get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO users (id, tenant_id, email, full_name, created_at)
            VALUES (:id, :tenant_id, :email, 'Alice 1', :created_at)
        """), {"id": subject_1.id, "tenant_id": tenant.id, "email": email, "created_at": t0})

    # 2. Intake erasure request for Subject 1 -> freezes cutoff_at
    req = await PlannerService.create_erasure_request(
        db_session, tenant.id, subject_1.id, quarantine_days=0
    )
    frozen_cutoff = req.cutoff_at

    # 3. User signs up again mid-saga -> Subject 2 created with same email, created_at > cutoff_at
    t2 = frozen_cutoff + timedelta(seconds=5)
    subject_2 = await SubjectService.register_subject(db_session, tenant.id, "EMAIL", email)
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO users (id, tenant_id, email, full_name, created_at)
            VALUES (:id, :tenant_id, :email, 'Alice 2 Recreated', :created_at)
        """), {"id": subject_2.id, "tenant_id": tenant.id, "email": email, "created_at": t2})

    # 4. Complete the erasure execution for Subject 1
    step_res = await SagaService.execute_next_step(db_session, tenant.id, req.id)
    assert step_res["state"] == "COMPLETED"

    # 5. Verify downstream table:
    # Subject 1 row MUST be erased
    # Subject 2 row MUST survive intact
    async with engine.connect() as conn:
        s1_row = (await conn.execute(text("SELECT id FROM users WHERE id = :id"), {"id": subject_1.id})).scalar()
        assert s1_row is None

        s2_row = (await conn.execute(text("SELECT email, full_name FROM users WHERE id = :id"), {"id": subject_2.id})).fetchone()
        assert s2_row is not None
        assert s2_row[0] == email
        assert s2_row[1] == "Alice 2 Recreated"

    await pg.close()
