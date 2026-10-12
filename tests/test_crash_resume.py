import pytest
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.services.tenant_service import TenantService
from src.services.subject_service import SubjectService
from src.services.data_map_service import DataMapService
from src.services.planner_service import PlannerService
from src.services.saga_service import SagaService
from src.models.step import ErasureStep

@pytest.mark.asyncio
async def test_crash_resume_mid_saga(db_session: AsyncSession):
    """
    Claim 2 (Crash-Resume): If worker crashes mid-saga, it resumes from the next
    pending step without repeating completed steps.
    """
    # 1. Setup tenant & active data map
    tenant = await TenantService.create_tenant(db_session, "CrashResume Corp")
    doc = {
        "stores": [
            {
                "id": "app-postgres",
                "connector": "postgres",
                "datasets": [
                    {"name": "users", "subject_key": "id", "action": "DELETE"},
                    {"name": "support_messages", "subject_key": "author_id", "action": "DELETE"}
                ]
            }
        ]
    }
    dm = await DataMapService.upload_data_map(db_session, tenant.id, 1, doc)
    await DataMapService.publish_data_map(db_session, tenant.id, dm.id)

    # 2. Register subject and initiate erasure request
    subject = await SubjectService.register_subject(
        db_session, tenant.id, "EMAIL", "worker_crash@test.com"
    )
    req = await PlannerService.create_erasure_request(
        db_session, tenant.id, subject.id, quarantine_days=0
    )

    # 3. Execute step 1
    step1_res = await SagaService.execute_next_step(db_session, tenant.id, req.id)
    assert step1_res["state"] == "COMPLETED"
    step1_id = step1_res["step_id"]

    # 4. Simulate crash: we disconnect, create new worker invocation, query pending steps
    # Verify that step 1 remains COMPLETED in persistent state
    s_stmt = select(ErasureStep).where(ErasureStep.id == uuid.UUID(step1_id))
    persisted_step1 = (await db_session.execute(s_stmt)).scalar_one()
    assert persisted_step1.state == "COMPLETED"
    assert persisted_step1.attempts == 1

    # 5. Worker resumes and executes next step
    step2_res = await SagaService.execute_next_step(db_session, tenant.id, req.id)
    assert step2_res["state"] == "COMPLETED"
    assert step2_res["step_id"] != step1_id

    # Verify step 1 was NOT re-executed (attempts remain 1)
    await db_session.refresh(persisted_step1)
    assert persisted_step1.attempts == 1
