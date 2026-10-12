import pytest
import uuid
from datetime import datetime
from sqlalchemy import text
from src.data_map.validator import DataMapValidator
from src.connectors.postgres import PostgresConnector
from src.data_map.models import ActionEnum

@pytest.mark.asyncio
async def test_anonymization_non_reversibility_and_hash_rejection():
    """
    Claim 5 (Anonymization Safety):
    1. A Data Map configured with a HASH strategy must be strictly rejected (pseudonymization != anonymization).
    2. Anonymized rows cannot be re-linked via a dictionary attack of known emails.
    """
    # 1. Negative test: Data Map validator rejects HASH strategy
    bad_doc = {
        "stores": [
            {
                "id": "bad-store",
                "connector": "postgres",
                "datasets": [
                    {
                        "name": "leads",
                        "subject_key": "id",
                        "action": "ANONYMIZE",
                        "anonymize": {
                            "email": "SHA256_HASH"
                        }
                    }
                ]
            }
        ]
    }
    with pytest.raises(ValueError, match="Hashing is pseudonymization, not anonymization under GDPR"):
        DataMapValidator.parse_and_validate(bad_doc)

    # 2. Test non-reversibility against dictionary lookup
    tenant_id = uuid.uuid4()
    subject_id = uuid.uuid4()
    known_email = "victim_target@acme-corp.com"
    candidate_dictionary = [
        "john@acme-corp.com",
        "sarah@acme-corp.com",
        "victim_target@acme-corp.com",
        "admin@acme-corp.com"
    ]

    pg = PostgresConnector()
    engine = await pg.get_engine()

    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO users (id, tenant_id, email, full_name, created_at)
            VALUES (:id, :tenant_id, :email, 'Victim User', :now)
        """), {"id": subject_id, "tenant_id": tenant_id, "email": known_email, "now": datetime.utcnow()})

    # Execute ANONYMIZE with RANDOM / NULLIFY
    await pg.erase(
        tenant_id=tenant_id,
        subject_id=subject_id,
        dataset="users",
        subject_key="id",
        action=ActionEnum.ANONYMIZE,
        cutoff_at=datetime.utcnow(),
        anonymize_rules={"email": "NULLIFY", "full_name": "RANDOM"}
    )

    # Query row after anonymization
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT email, full_name FROM users WHERE id = :id"), {"id": subject_id})).fetchone()
        anon_email = row[0]
        anon_name = row[1]

    # Verify email is nullified
    assert anon_email is None

    # Verify dictionary attack cannot re-identify the subject by email
    dictionary_match = any(candidate == anon_email for candidate in candidate_dictionary)
    assert dictionary_match is False

    # Verify name was randomized and doesn't match original
    assert anon_name != "Victim User"
    assert anon_name.startswith("ANON_")

    await pg.close()
