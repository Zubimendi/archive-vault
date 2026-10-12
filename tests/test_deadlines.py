import pytest
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from src.services.tenant_service import TenantService
from src.services.subject_service import SubjectService
from src.services.data_map_service import DataMapService
from src.services.planner_service import PlannerService
from src.services.deadlines_service import DeadlinesService
from src.services.ledger_service import LedgerService

@pytest.mark.asyncio
async def test_deadlines_escalation_and_lawful_extension(db_session: AsyncSession):
    """
    Claim 12 (Deadlines & Escalation Engine):
    - T-7 fires AT_RISK state.
    - Expired deadlines transition to OVERDUE.
    - OVERDUE cannot be silently cleared; requires lawful basis & logged audit trail.
    """
    tenant = await TenantService.create_tenant(db_session, "Deadlines Engine Corp")
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

    # 1. Create Request 1 (at T-6 days) and Request 2 (expired, -2 days)
    s1 = await SubjectService.register_subject(db_session, tenant.id, "EMAIL", "t7@test.com")
    s2 = await SubjectService.register_subject(db_session, tenant.id, "EMAIL", "overdue@test.com")

    now = datetime.utcnow()
    req1 = await PlannerService.create_erasure_request(db_session, tenant.id, s1.id, quarantine_days=0)
    req1.due_at = now + timedelta(days=6)

    req2 = await PlannerService.create_erasure_request(db_session, tenant.id, s2.id, quarantine_days=0)
    req2.due_at = now - timedelta(days=2)

    await db_session.commit()

    # 2. Run deadline sweep
    sweep_res = await DeadlinesService.sweep_deadlines(db_session, tenant.id)
    assert sweep_res["at_risk_t7"] >= 1
    assert sweep_res["overdue"] >= 1

    await db_session.refresh(req1)
    await db_session.refresh(req2)
    assert req1.state == "AT_RISK"
    assert req2.state == "OVERDUE"

    # 3. Attempt extension without legal basis -> Must reject
    with pytest.raises(ValueError, match="Legal basis must be declared"):
        await DeadlinesService.extend_deadline(
            session=db_session,
            tenant_id=tenant.id,
            request_id=req2.id,
            extension_days=30,
            legal_basis="",
            subject_notified=True,
            authorized_by="dpo@company.com"
        )

    # 4. Lawful extension under GDPR Article 12(3)
    ext_res = await DeadlinesService.extend_deadline(
        session=db_session,
        tenant_id=tenant.id,
        request_id=req2.id,
        extension_days=30,
        legal_basis="GDPR Article 12(3) Complexity Extension",
        subject_notified=True,
        authorized_by="dpo@company.com"
    )
    assert ext_res["extension_days"] == 30
    assert req2.state != "OVERDUE"

    # Verify ledger event was logged
    events = await LedgerService.get_events(db_session, tenant.id, req2.id)
    ext_event = next((e for e in events if e.event_type == "DEADLINE_EXTENDED"), None)
    assert ext_event is not None
    assert ext_event.payload["legal_basis"] == "GDPR Article 12(3) Complexity Extension"
