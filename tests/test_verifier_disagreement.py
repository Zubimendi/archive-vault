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
from src.services.verifier_service import VerifierService

@pytest.mark.asyncio
async def test_verifier_disagreement_halts_completion(db_session: AsyncSession):
    """
    Claim 8 (Verifier Disagreement):
    If an independent read-scan detects a remnant row that survived erasure,
    the verifier fails the request, keeps state in PARTIALLY_COMPLETED, and prevents completion.
    """
    tenant = await TenantService.create_tenant(db_session, "Verifier Test Corp")
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
        db_session, tenant.id, "EMAIL", "verifier_victim@test.com"
    )

    req = await PlannerService.create_erasure_request(
        db_session, tenant.id, subject.id, quarantine_days=0
    )

    # Execute purge step
    await SagaService.execute_next_step(db_session, tenant.id, req.id)

    # Plant a remnant row in downstream database (mimicking an unpurged partition or replica lag)
    pg = PostgresConnector()
    engine = await pg.get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO users (id, tenant_id, email, full_name, created_at)
            VALUES (:id, :tenant_id, 'remnant@test.com', 'Remnant Row', :t)
        """), {
            "id": subject.id,
            "tenant_id": tenant.id,
            "t": req.cutoff_at - timedelta(seconds=10)
        })

    # Trigger independent verification scan
    verify_res = await VerifierService.verify_request(
        session=db_session,
        tenant_id=tenant.id,
        request_id=req.id
    )

    # Assert verifier caught the discrepancy
    assert verify_res["verified"] is False
    assert verify_res["state"] == "PARTIALLY_COMPLETED"
    assert len(verify_res["discrepancies"]) >= 1
    assert req.state != "COMPLETED"

    await pg.close()
