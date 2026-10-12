import pytest
import uuid
from datetime import datetime
from sqlalchemy import text
from src.data_map.models import ActionEnum
from src.connectors.postgres import PostgresConnector

@pytest.mark.asyncio
async def test_erasure_steps_idempotency():
    """
    Claim 1 (Idempotency): Running every step type twice produces identical end states with zero errors.
    """
    tenant_id = uuid.uuid4()
    subject_id = uuid.uuid4()
    now = datetime.utcnow()

    pg = PostgresConnector()
    engine = await pg.get_engine()

    # Seed downstream test tables
    async with engine.begin() as conn:
        # Seed users table (for DELETE test)
        await conn.execute(text("""
            INSERT INTO users (id, tenant_id, email, full_name, phone, created_at)
            VALUES (:id, :tenant_id, 'idem_user@example.com', 'Idem Test', '123456', :now)
        """), {"id": subject_id, "tenant_id": tenant_id, "now": now})

        # Seed support_messages table (for ANONYMIZE test)
        msg_id = uuid.uuid4()
        await conn.execute(text("""
            INSERT INTO support_messages (id, tenant_id, author_id, content, created_at)
            VALUES (:id, :tenant_id, :author_id, 'Sensitive complaint content', :now)
        """), {"id": msg_id, "tenant_id": tenant_id, "author_id": subject_id, "now": now})

    # 1. Test DELETE idempotency
    res1 = await pg.erase(
        tenant_id=tenant_id,
        subject_id=subject_id,
        dataset="users",
        subject_key="id",
        action=ActionEnum.DELETE,
        cutoff_at=now
    )
    assert res1["action"] == "DELETE"
    assert res1["rows_affected"] == 1

    # Second DELETE execution on same subject
    res2 = await pg.erase(
        tenant_id=tenant_id,
        subject_id=subject_id,
        dataset="users",
        subject_key="id",
        action=ActionEnum.DELETE,
        cutoff_at=now
    )
    assert res2["action"] == "DELETE"
    assert res2["rows_affected"] == 0

    # 2. Test ANONYMIZE idempotency
    res_anon1 = await pg.erase(
        tenant_id=tenant_id,
        subject_id=subject_id,
        dataset="support_messages",
        subject_key="author_id",
        action=ActionEnum.ANONYMIZE,
        cutoff_at=now,
        anonymize_rules={"content": "NULLIFY"}
    )
    assert res_anon1["action"] == "ANONYMIZE"
    assert res_anon1["rows_affected"] == 1

    res_anon2 = await pg.erase(
        tenant_id=tenant_id,
        subject_id=subject_id,
        dataset="support_messages",
        subject_key="author_id",
        action=ActionEnum.ANONYMIZE,
        cutoff_at=now,
        anonymize_rules={"content": "NULLIFY"}
    )
    assert res_anon2["action"] == "ANONYMIZE"
    assert res_anon2["rows_affected"] == 1

    # Verify end state of support_messages
    async with engine.connect() as conn:
        q = text("SELECT content FROM support_messages WHERE id = :id")
        val = (await conn.execute(q, {"id": msg_id})).scalar()
        assert val is None

    # 3. Test Verify idempotency
    v1 = await pg.verify(
        tenant_id=tenant_id,
        subject_id=subject_id,
        dataset="users",
        subject_key="id",
        cutoff_at=now,
        action=ActionEnum.DELETE
    )
    v2 = await pg.verify(
        tenant_id=tenant_id,
        subject_id=subject_id,
        dataset="users",
        subject_key="id",
        cutoff_at=now,
        action=ActionEnum.DELETE
    )
    assert v1["verified"] is True
    assert v2["verified"] is True
    assert v1 == v2

    await pg.close()
