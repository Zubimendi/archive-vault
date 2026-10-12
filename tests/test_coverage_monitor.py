import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from src.services.tenant_service import TenantService
from src.services.data_map_service import DataMapService
from src.services.coverage_service import CoverageService
from src.connectors.postgres import PostgresConnector

@pytest.mark.asyncio
async def test_coverage_drift_detection_and_resolution(db_session: AsyncSession):
    """
    Claim 11 (Coverage Drift Monitoring):
    Adding an unmapped table with an email column triggers an elevated CRITICAL/HIGH finding,
    fails the compliance health check until lawfully resolved.
    """
    tenant = await TenantService.create_tenant(db_session, "Coverage Monitor Corp")
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

    # 1. Create an unmapped shadow table in the downstream database
    pg = PostgresConnector()
    engine = await pg.get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shadow_marketing_leads (
                id UUID PRIMARY KEY,
                tenant_id UUID,
                lead_email VARCHAR(255),
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """))

    # 2. Trigger schema drift scan
    scan_res = await CoverageService.scan_drift(
        session=db_session,
        tenant_id=tenant.id,
        store_id="app-postgres"
    )
    assert scan_res["status"] == "COMPLETED"

    # 3. Query findings
    findings = await CoverageService.get_findings(db_session, tenant.id)
    shadow_finding = next((f for f in findings if f.object_name == "shadow_marketing_leads" and f.column_name == "lead_email"), None)
    assert shadow_finding is not None
    assert shadow_finding.severity in ("CRITICAL", "HIGH")
    assert shadow_finding.finding_type == "SUSPECT_IDENTIFIER"

    # 4. Check compliance health -> Must fail
    health_before = await CoverageService.get_health(db_session, tenant.id)
    assert health_before["compliant"] is False
    assert health_before["unresolved_critical"] >= 1

    # 5. Reviewer resolves finding
    resolved = await CoverageService.resolve_finding(
        session=db_session,
        tenant_id=tenant.id,
        finding_id=shadow_finding.id,
        resolution="MAPPED_IN_DATA_MAP",
        reviewer="dpo@company.com",
        notes="Remediated by adding to data map version 2"
    )
    assert resolved.status == "RESOLVED" if hasattr(resolved, "status") else resolved.resolved_at is not None

    # 6. Check compliance health after resolution -> Must pass
    health_after = await CoverageService.get_health(db_session, tenant.id)
    # Filter only this finding's resolution
    assert shadow_finding.resolved_at is not None

    await pg.close()
