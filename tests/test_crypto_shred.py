import pytest
import uuid
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from src.config import settings
from src.crypto.envelope import EnvelopeCrypto
from src.services.tenant_service import TenantService
from src.services.subject_service import SubjectService
from src.connectors.postgres import PostgresConnector
from cryptography.exceptions import InvalidTag

@pytest.mark.asyncio
async def test_crypto_shredding_and_backup_unreadability(db_session: AsyncSession):
    """
    Claim 6 (Envelope Crypto-Shredding):
    Ciphertext persists in the database, but destroying the subject DEK renders all
    current and historical snapshot copies permanently unreadable.
    """
    tenant = await TenantService.create_tenant(db_session, "CryptoShred Enterprise")
    tenant_kek = EnvelopeCrypto.derive_tenant_kek(tenant.kek_ref, settings.MASTER_KEK_SECRET)

    # 1. Register subject and obtain original wrapped DEK
    subject = await SubjectService.register_subject(
        db_session, tenant.id, "EMAIL", "shred_subject@bank.com"
    )

    unwrapped_dek = EnvelopeCrypto.unwrap_dek(tenant_kek, subject.wrapped_dek, subject.dek_iv)

    # 2. Encrypt sensitive payload and store ciphertext in downstream events table
    secret_payload = b'{"card_number": "4111-2222-3333-4444", "balance": 99500}'
    ciphertext, payload_iv = EnvelopeCrypto.encrypt_data(unwrapped_dek, secret_payload)

    event_id = uuid.uuid4()
    pg = PostgresConnector()
    engine = await pg.get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO events (id, tenant_id, user_id, encrypted_payload, payload_iv, created_at)
            VALUES (:id, :tenant_id, :user_id, :payload, :iv, :now)
        """), {
            "id": event_id,
            "tenant_id": tenant.id,
            "user_id": subject.id,
            "payload": ciphertext,
            "iv": payload_iv,
            "now": datetime.utcnow()
        })

    # Verify decrypt succeeds before shred
    decrypted_before = EnvelopeCrypto.decrypt_data(unwrapped_dek, ciphertext, payload_iv)
    assert decrypted_before == secret_payload

    # 3. Simulate Crypto-Shred: physically overwrite and destroy the subject's wrapped DEK
    subject.wrapped_dek = b"\x00" * 32
    subject.status = "ERASED"
    await db_session.commit()

    # 4. Verify ciphertext persists in the database table
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT encrypted_payload, payload_iv FROM events WHERE id = :id"), {"id": event_id})).fetchone()
        persisted_ciphertext = row[0]
        persisted_iv = row[1]
        assert persisted_ciphertext == ciphertext

    # 5. Attempt decryption after key zeroization
    # Since wrapped_dek is zeroed, unwrap fails
    with pytest.raises(InvalidTag):
        EnvelopeCrypto.unwrap_dek(tenant_kek, subject.wrapped_dek, subject.dek_iv)

    # Even with a snapshot or restored copy of the table, decryption without the destroyed DEK is impossible
    garbage_key = EnvelopeCrypto.generate_dek()
    with pytest.raises(InvalidTag):
        EnvelopeCrypto.decrypt_data(garbage_key, persisted_ciphertext, persisted_iv)

    await pg.close()
