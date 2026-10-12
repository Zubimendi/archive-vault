import asyncio
from sqlalchemy import text
from src.database import engine, Base
from src.config import settings
from src.models import *
from sqlalchemy.ext.asyncio import create_async_engine

TRIGGER_STATEMENTS = [
    """
    CREATE OR REPLACE FUNCTION check_data_map_immutability()
    RETURNS TRIGGER AS $$
    BEGIN
        IF OLD.status = 'ACTIVE' THEN
            RAISE EXCEPTION 'Active data map version cannot be modified or deleted';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """,
    "DROP TRIGGER IF EXISTS trg_data_map_immutable ON data_map_versions;",
    """
    CREATE TRIGGER trg_data_map_immutable
    BEFORE UPDATE OR DELETE ON data_map_versions
    FOR EACH ROW EXECUTE FUNCTION check_data_map_immutability();
    """,
    """
    CREATE OR REPLACE FUNCTION prevent_ledger_modification()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'Erasure ledger is append-only. UPDATE and DELETE operations are forbidden.';
    END;
    $$ LANGUAGE plpgsql;
    """,
    "DROP TRIGGER IF EXISTS trg_ledger_append_only ON erasure_ledger;",
    """
    CREATE TRIGGER trg_ledger_append_only
    BEFORE UPDATE OR DELETE ON erasure_ledger
    FOR EACH ROW EXECUTE FUNCTION prevent_ledger_modification();
    """
]

DOWNSTREAM_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        email VARCHAR(255),
        full_name VARCHAR(255),
        phone VARCHAR(64),
        is_quarantined BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS support_messages (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        author_id UUID NOT NULL,
        content TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS invoices (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        customer_id UUID NOT NULL,
        amount_cents INT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        user_id UUID NOT NULL,
        encrypted_payload BYTEA,
        payload_iv BYTEA,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS contacts (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        email VARCHAR(255),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
]

async def init_databases():
    print("Initializing ArchiveVault primary database schema...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in TRIGGER_STATEMENTS:
            cleaned = stmt.strip()
            if cleaned:
                await conn.execute(text(cleaned))
    print("ArchiveVault primary schema and triggers created successfully.")

    print("Initializing downstream application test database...")
    downstream_engine = create_async_engine(settings.DOWNSTREAM_DB_URL, future=True)
    async with downstream_engine.begin() as dconn:
        for stmt in DOWNSTREAM_TABLES:
            cleaned = stmt.strip()
            if cleaned:
                await dconn.execute(text(cleaned))
    await downstream_engine.dispose()
    print("Downstream database schema created successfully.")

if __name__ == "__main__":
    asyncio.run(init_databases())
