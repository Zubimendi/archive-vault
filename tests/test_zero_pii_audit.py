import pytest
import json
from sqlalchemy.ext.asyncio import AsyncSession
from src.services.tenant_service import TenantService
from src.services.subject_service import SubjectService
from src.services.data_map_service import DataMapService
from src.services.planner_service import PlannerService
from src.services.saga_service import SagaService
from src.services.verifier_service import VerifierService
from src.services.certificate_service import CertificateService
from src.services.ledger_service import LedgerService

@pytest.mark.asyncio
async def test_zero_pii_audit_in_ledger_and_certificates(db_session: AsyncSession):
    """
    Claim 10 (The Proof Paradox / Zero-PII Audit):
    Full text scan of all ledger payloads and compliance certificates must contain
    zero occurrences of the subject's plaintext email or name.
    """
    sensitive_email = "jane.secret@audit.com"
    sensitive_name = "Jane Audited"

    tenant = await TenantService.create_tenant(db_session, "Zero PII Bank")
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

    # Register subject
    subject = await SubjectService.register_subject(
        db_session, tenant.id, "EMAIL", sensitive_email
    )

    # Create and execute erasure request
    req = await PlannerService.create_erasure_request(
        db_session, tenant.id, subject.id, quarantine_days=0
    )

    await SagaService.execute_next_step(db_session, tenant.id, req.id)
    await VerifierService.verify_request(db_session, tenant.id, req.id)

    # Generate certificate
    certificate = await CertificateService.generate_certificate(db_session, tenant.id, req.id)
    cert_text = json.dumps(certificate).lower()

    # Query all ledger events for this request
    ledger_events = await LedgerService.get_events(db_session, tenant.id, req.id)
    ledger_text = json.dumps([e.payload for e in ledger_events]).lower()

    # Full text scan for personal identifiers
    assert sensitive_email.lower() not in cert_text
    assert sensitive_name.lower() not in cert_text
    assert "@" not in cert_text

    assert sensitive_email.lower() not in ledger_text
    assert sensitive_name.lower() not in ledger_text
    assert "@" not in ledger_text
