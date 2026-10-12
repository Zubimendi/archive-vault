# ArchiveVault MVP: Comprehensive Implementation Plan

> **Specification Reference:** `docs/ARCHITECTURE.MD` and `docs/CURSOR_CONTEXT.MD`  
> **Target Framework:** Python 3.12 + FastAPI + SQLAlchemy 2.0 (Async) + asyncpg + PostgreSQL 16 + Cryptography + QueueLine  

---

## 1. Executive Summary & Architectural Invariants

ArchiveVault is a compliance and data lifecycle engine designed to solve the distributed data erasure problem under GDPR (Article 17) and CCPA/CPRA. Unlike naive CRUD architectures that scatter `DELETE` queries across services, ArchiveVault provides:
1. **Retention policy as declarative config:** A versioned, immutable Data Map.
2. **Erasure as a 3-phase saga:** Reversible `QUARANTINE` (protecting against forged deletion attacks) $\to$ explicit irrevocable `PIVOT` $\to$ forward-only retriable `PURGE` & `PROPAGATE`.
3. **True Envelope Crypto-Shredding:** Per-subject Data Encryption Keys (DEKs) wrapped under per-tenant Key Encryption Keys (KEKs).
4. **The Proof Paradox Resolution:** Independent re-verification and a SHA-256 hash-chained append-only ledger that guarantees **Zero-PII retention** in proofs and certificates.
5. **Continuous Coverage Monitoring:** Active database schema introspection against the Data Map to eliminate unmapped shadow PII tables/columns.

### Non-Negotiable Architectural Decisions (Do Not Relitigate)
1. **Four distinct actions:** `DELETE`, `ANONYMIZE`, `CRYPTO_SHRED`, and `RETAIN_WITH_REASON`. They are never conflated.
2. **Saga directionality:** Phase 1 (`QUARANTINE`) is compensable; Phase 2 (`PURGE`) and Phase 3 (`PROPAGATE`) are strictly forward-only retriable with no compensating rollback.
3. **Envelope key hierarchy:** Per-subject DEKs wrapped by per-tenant KEKs; never tenant-wide keys alone.
4. **Anonymization safety:** Never use hashes (`SHA-256(email)` is pseudonymization, not anonymization). Only `NULLIFY`, random replacement, or generalization are permitted. Keyed HMAC is reserved exclusively for the suppression ledger with a secret pepper.
5. **Proof paradox:** Neither the erasure ledger nor the issued certificates may store raw user identifiers or deleted values.
6. **Subject isolation:** Subjects are located via stable internal keys (UUID) combined with a frozen `cutoff_at` timestamp to eliminate the subject-recreation race.

---

## 2. System Architecture & Component Topology

```
                      ┌───────────────────────────────────────────────┐
                      │              FastAPI API Layer                │
                      │  (Intake, Holds, Certs, Data Maps, Health)    │
                      └──────────────────────┬────────────────────────┘
                                             │
                      ┌──────────────────────▼────────────────────────┐
                      │    Request Intake & Planning Engine           │
                      │  - Deduplication per (tenant, subject)        │
                      │  - Deadline assignment via Regime Profile     │
                      │  - Snapshot & freeze cutoff_at timestamp      │
                      │  - Plan generation from immutable Data Map    │
                      └──────────────────────┬────────────────────────┘
                                             │
                      ┌──────────────────────▼────────────────────────┐
                      │          ArchiveVault Core Database           │
                      │   - Tenants, Subjects, Data Maps              │
                      │   - Erasure Requests & Steps                  │
                      │   - Append-Only Hash-Chained Ledger           │
                      │   - Suppression Ledger (HMAC)                 │
                      └──────────────┬────────────────┬───────────────┘
                                     │                │
            ┌────────────────────────▼──┐          ┌──▼─────────────────────────┐
            │   Saga Executor Worker    │          │  Background Schedulers     │
            │   - QueueLine worker      │          │  - Deadline Escalation     │
            │   - State transition lock │          │  - Retention Sweep         │
            │   - Pre-pivot cancel      │          │  - Coverage Drift Monitor  │
            │   - Forward-only retry    │          │  - Restore-Replay Engine   │
            └────────────┬──────────────┘          └────────────────────────────┘
                         │
      ┌──────────────────┼─────────────────────────┐
      ▼                  ▼                         ▼
┌──────────────┐  ┌──────────────┐          ┌──────────────┐
│  Postgres    │  │  Crypto-     │          │  Webhook /   │
│  Connector   │  │  Shredder    │          │  Manual      │
│ (Target DBs) │  │ (DEK Delete) │          │  Connectors  │
└──────────────┘  └──────────────┘          └──────────────┘
```

---

## 3. Database Schema & Data Models (Step 1)

All tables enforce strict tenant isolation via `tenant_id` foreign keys and row-level filtering.

### 3.1 DDL Specification

```sql
-- 1. Tenants
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    regime_profile VARCHAR(32) NOT NULL DEFAULT 'GDPR', -- 'GDPR' (30d), 'CCPA' (45d), 'CUSTOM'
    kek_ref VARCHAR(255) NOT NULL, -- KMS URI or Vault reference
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Subjects (Opaque ID; absolutely NO plaintext PII)
CREATE TABLE subjects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    wrapped_dek BYTEA NOT NULL, -- AES-256-GCM encrypted DEK under tenant KEK
    dek_iv BYTEA NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- ACTIVE, QUARANTINED, ERASED
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_subject UNIQUE (tenant_id, id)
);

-- 3. Data Map Versions (Immutable once published)
CREATE TABLE data_map_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    version INT NOT NULL,
    document JSONB NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT', -- DRAFT, ACTIVE, ARCHIVED
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_version UNIQUE (tenant_id, version)
);

-- Immutability Trigger for ACTIVE data map versions
CREATE OR REPLACE FUNCTION check_data_map_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status = 'ACTIVE' THEN
        RAISE EXCEPTION 'Active data map version cannot be modified or deleted';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_data_map_immutable
BEFORE UPDATE OR DELETE ON data_map_versions
FOR EACH ROW EXECUTE FUNCTION check_data_map_immutability();

-- 4. Erasure Requests
CREATE TABLE erasure_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    subject_id UUID NOT NULL REFERENCES subjects(id),
    trigger VARCHAR(32) NOT NULL, -- SUBJECT_REQUEST, RETENTION_SWEEP, TENANT_OFFBOARD
    regime VARCHAR(32) NOT NULL,
    data_map_version_id UUID NOT NULL REFERENCES data_map_versions(id),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    due_at TIMESTAMPTZ NOT NULL,
    cutoff_at TIMESTAMPTZ NOT NULL,
    quarantine_until TIMESTAMPTZ,
    state VARCHAR(32) NOT NULL DEFAULT 'RECEIVED',
    -- RECEIVED, PLANNED, QUARANTINED, CANCELLED, HELD, PURGING, PROPAGATING, VERIFYING, COMPLETED, PARTIALLY_COMPLETED, AT_RISK, OVERDUE
    cancellation_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5. Erasure Steps (Persisted Saga Execution Steps)
CREATE TABLE erasure_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL REFERENCES erasure_requests(id) ON DELETE CASCADE,
    store_id VARCHAR(64) NOT NULL,
    dataset VARCHAR(64) NOT NULL,
    phase VARCHAR(32) NOT NULL, -- QUARANTINE, PURGE, PROPAGATE
    action VARCHAR(32) NOT NULL, -- DELETE, ANONYMIZE, CRYPTO_SHRED, RETAIN_WITH_REASON
    state VARCHAR(32) NOT NULL DEFAULT 'PENDING', -- PENDING, IN_PROGRESS, COMPLETED, FAILED, COMPENSATING, CANCELLED
    attempts INT NOT NULL DEFAULT 0,
    evidence JSONB, -- non-PII verification metadata (rows affected, checksums)
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. Suppression Ledger (HMAC with Pepper for restore-replay protection)
CREATE TABLE suppression_ledger (
    subject_hmac VARCHAR(128) PRIMARY KEY, -- HMAC-SHA256(pepper, normalized_identifier)
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    erased_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_id UUID NOT NULL REFERENCES erasure_requests(id)
);

-- 7. Erasure Ledger (Cryptographically Tamper-Evident Hash Chain)
CREATE TABLE erasure_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    seq BIGSERIAL NOT NULL,
    request_id UUID NOT NULL REFERENCES erasure_requests(id),
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL, -- ABSOLUTELY ZERO PII
    prev_hash VARCHAR(64) NOT NULL,
    hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ledger_seq UNIQUE (tenant_id, seq)
);

-- Append-only trigger preventing ledger modifications or deletes
CREATE OR REPLACE FUNCTION prevent_ledger_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Erasure ledger is append-only. UPDATE and DELETE operations are forbidden.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ledger_append_only
BEFORE UPDATE OR DELETE ON erasure_ledger
FOR EACH ROW EXECUTE FUNCTION prevent_ledger_modification();

-- 8. Legal Holds
CREATE TABLE legal_holds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    subject_id UUID NOT NULL REFERENCES subjects(id),
    reason TEXT NOT NULL,
    placed_by VARCHAR(255) NOT NULL,
    placed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    released_by VARCHAR(255),
    release_reason TEXT
);

-- 9. Coverage Findings
CREATE TABLE coverage_findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    store_id VARCHAR(64) NOT NULL,
    object_name VARCHAR(255) NOT NULL,
    column_name VARCHAR(255) NOT NULL,
    finding_type VARCHAR(64) NOT NULL, -- UNMAPPED_COLUMN, UNMAPPED_TABLE, SUSPECT_IDENTIFIER, FK_TO_SUBJECT
    severity VARCHAR(16) NOT NULL, -- LOW, MEDIUM, HIGH, CRITICAL
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    resolution VARCHAR(64), -- MAPPED_IN_DATA_MAP, NOT_PERSONAL_DATA, FALSE_POSITIVE
    reviewer VARCHAR(255),
    notes TEXT
);
```

---

## 4. Phase-by-Phase Implementation Blueprint (Step 2)

### Milestone 1: Data Map Loader & Validator (§1, §6)
- **Path:** `src/core/data_map/`
- **Responsibilities:**
  - Parse YAML/JSON data map schemas with Pydantic v2.
  - Enforce schema contracts: `stores`, `datasets`, `subject_key`, `action`, `retention`.
  - Validate Anonymization Strategies: Strict allow-list (`NULLIFY`, `RANDOM`, `GENERALIZE`). **Reject** any configuration attempting `HASH` or `PSEUDONYMIZE`.
  - Check dataset dependency DAG for referential integrity during cascading drops.
  - Immutability enforcement via API validation and database triggers.

### Milestone 2: Connector Interface & Postgres Target Connector (§9)
- **Path:** `src/connectors/`
- **Abstract Base Class `BaseConnector`:**
  - `async def locate(self, tenant_id: UUID, subject_id: UUID, cutoff_at: datetime) -> list[RecordLoc]:`
  - `async def quarantine(self, tenant_id: UUID, subject_id: UUID, cutoff_at: datetime) -> StepResult:`
  - `async def erase(self, tenant_id: UUID, subject_id: UUID, cutoff_at: datetime, action: ActionEnum, params: dict) -> StepResult:`
  - `async def verify(self, tenant_id: UUID, subject_id: UUID, cutoff_at: datetime) -> VerificationResult:`
  - `async def compensate_quarantine(self, tenant_id: UUID, subject_id: UUID) -> StepResult:`
- **PostgresConnector:**
  - Connects to downstream application databases via asyncpg.
  - Executes parameterized atomic batch deletes and anonymization updates with cutoff filters: `WHERE subject_id = $1 AND created_at <= $2`.
  - Built-in verification queries checking `COUNT(*) == 0`.

### Milestone 3: Request Intake, Deduplication & Planner (§2, §3, §4, §10)
- **Path:** `src/core/intake/` and `src/core/planner/`
- **Intake Service:**
  - Deduplicate active requests: If an in-flight request exists for `(tenant_id, subject_id)` in non-terminal state, reject or coalesce.
  - Assign legal clock `due_at`:
    - GDPR: `received_at + 30 days`
    - CCPA: `received_at + 45 days`
  - Compute `quarantine_until`: `received_at + quarantine_window_days` (default 3 days for subject requests; 0 days for immediate retention sweeps/tenant offboards).
  - Freeze `cutoff_at = received_at`: Guarantees isolation from future signups.
  - Pin the current `ACTIVE` `data_map_version_id`.
- **Planner Service:**
  - Expand the pinned Data Map into `erasure_steps`:
    - **Phase 1 (QUARANTINE):** For all stores supporting soft-lock/quarantine.
    - **Phase 2 (PURGE):** `DELETE`, `ANONYMIZE`, `CRYPTO_SHRED`.
    - **Phase 3 (PROPAGATE):** Third-party webhooks, manual tasks.

### Milestone 4: Saga Execution Engine & State Machine (§3, §4)
- **Path:** `src/core/saga/`
- **Execution Engine:**
  - Integrates with QueueLine job dispatching.
  - Persists step state in PostgreSQL with row-level locks (`SELECT FOR UPDATE`).
  - **Explicit Transition Table:**
    - `RECEIVED` $\to$ `PLANNED` $\to$ `QUARANTINED`
    - In `QUARANTINED`: If cancellation received prior to `quarantine_until`, invoke `compensate_quarantine` on steps, transition to `CANCELLED`.
    - **The Pivot Point:** When `now() >= quarantine_until`, transition atomically to `PURGING`. Once in `PURGING`, cancellation is rejected with `409 Conflict`.
    - In `PURGING` / `PROPAGATING`: Steps execute with exponential backoff on retriable errors. State cannot revert.
    - Post-purge: Transition to `VERIFYING`.

### Milestone 5: Envelope Encryption for CRYPTO_SHRED (§5)
- **Path:** `src/crypto/`
- **Mechanism:**
  - Tenant KEK: 256-bit AES master key retrieved from tenant configuration or KMS mock.
  - Subject DEK: Unique 256-bit AES-GCM key generated per subject at registration.
  - `wrapped_dek = AES_GCM_Encrypt(tenant_kek, subject_dek)`.
- **Shredding Procedure:**
  - Physical zeroization / overwrite and deletion of `wrapped_dek` and `dek_iv` in `subjects`.
- **Crypto-Shred Verifier:**
  - Attempts decryption of sample or known dataset block using destroyed key. Verifies `KeyNotFoundException` or decrypt auth tag failure.

### Milestone 6: Verifier, Hash-Chained Ledger & Zero-PII Certificate (§7)
- **Path:** `src/core/ledger/` and `src/core/verifier/`
- **Tamper-Evident Ledger:**
  - `hash = SHA256(prev_hash + seq + tenant_id + request_id + event_type + payload_json + created_at)`.
  - Genesis block per tenant initialized at tenant creation.
  - Chain validation routine checks all sequential blocks for tamper detection.
- **Proof Paradox Enforcer:**
  - Strict Pydantic sanitizer for ledger and certificate payloads: Strip any field matching email, name, IP, phone, street, SSN, or arbitrary string values.
  - Ledgers only record: step counts, affected row counts, store identifiers, and status codes.
- **Certificate Generator:**
  - Generates verifiable PDF/JSON compliance certificate containing: `certificate_id`, `request_id`, `tenant_id`, `execution_window`, `actions_summary`, `retained_datasets_with_legal_basis`, `ledger_leaf_hash`, and digital signature.

### Milestone 7: Suppression Ledger & Restore-Replay Service (§7)
- **Path:** `src/core/suppression/`
- **Mechanism:**
  - On purge completion: Compute `HMAC-SHA256(secret_pepper, normalized_email)`.
  - Store `(subject_hmac, tenant_id, erased_at, request_id)` in `suppression_ledger`.
- **Restore-Replay Job:**
  - Background worker that inspects newly restored snapshots or periodic database dumps.
  - Re-calculates HMACs of database records; if a match exists in `suppression_ledger`, immediately triggers an automated `RETENTION_SWEEP` purge for that subject.

### Milestone 8: Coverage Drift Monitor (§8)
- **Path:** `src/core/coverage/`
- **Introspection Engine:**
  - Scans target PostgreSQL `information_schema.columns` and `information_schema.tables`.
  - Compares existing physical tables and columns against the active Data Map.
  - Raises `CoverageFinding`:
    - `CRITICAL`: Unmapped column matching regex `(?i)(email|ssn|phone|password|secret|dob)`.
    - `HIGH`: Unmapped table with foreign key reference to mapped subject table.
    - `MEDIUM`: Unmapped column in mapped table.
- **Compliance Health Check:**
  - Endpoint `GET /api/v1/coverage/health` reports status `FAIL` if any `CRITICAL` or `HIGH` finding remains unresolved past the allowed SLA (e.g. 7 days).

### Milestone 9: Deadline Escalation & Legal Holds (§10, §11)
- **Path:** `src/core/deadlines/` and `src/core/holds/`
- **Escalation Job:**
  - Daily sweep of open requests:
    - `due_at - now() <= 14 days`: Issue T-14 notification event.
    - `due_at - now() <= 7 days`: Issue T-7 notification event, flag `AT_RISK` if retrying.
    - `now() > due_at`: Transition status to `OVERDUE`.
- **Legal Holds:**
  - `POST /api/v1/holds`: Creates hold, immediately pauses saga into `HELD`.
  - `POST /api/v1/holds/{id}/release`: Releases hold, logs reason, resumes saga into `PLANNED`.

### Milestone 10: Webhook & Manual Connectors (§9)
- **Path:** `src/connectors/webhook.py` and `src/connectors/manual.py`
- **Webhook Connector:**
  - Sends signed HMAC-SHA256 deletion payloads to third-party processors via QueueLine.
  - Awaits `200 OK` or async callback acknowledgment.
- **Manual Connector:**
  - Creates manual ticket record; transitions step to `AWAITING_MANUAL_EVIDENCE`.
  - Requires operator to upload signed proof/receipt prior to step completion.

---

## 5. API Route Specification (REST)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/tenants` | Onboard new tenant with regime profile and KEK ref |
| `GET` | `/api/v1/tenants/{tenant_id}` | Get tenant details and current compliance status |
| `POST` | `/api/v1/tenants/{tenant_id}/offboard` | Trigger full tenant offboarding and KEK destruction |
| `POST` | `/api/v1/subjects` | Register new subject with encrypted DEK |
| `GET` | `/api/v1/subjects/{subject_id}` | Retrieve subject status (opaque UUID only) |
| `POST` | `/api/v1/data-maps` | Upload & validate new data map document |
| `POST` | `/api/v1/data-maps/{version_id}/publish`| Atomically activate data map version |
| `GET` | `/api/v1/data-maps/active` | Get current active data map version |
| `POST` | `/api/v1/erasure-requests` | Intake new erasure request (subject/sweep/offboard) |
| `GET` | `/api/v1/erasure-requests/{id}` | Inspect request state, steps, and timeline |
| `POST` | `/api/v1/erasure-requests/{id}/cancel` | Pre-pivot quarantine cancellation |
| `POST` | `/api/v1/erasure-requests/{id}/execute-step` | Worker trigger for executing next pending step |
| `GET` | `/api/v1/erasure-requests/{id}/certificate` | Generate Zero-PII Erasure Certificate |
| `GET` | `/api/v1/ledger` | Inspect tamper-evident ledger events |
| `GET` | `/api/v1/ledger/verify` | Verify complete cryptographic hash chain |
| `POST` | `/api/v1/holds` | Place legal hold on subject |
| `POST` | `/api/v1/holds/{id}/release` | Release legal hold with audit reason |
| `POST` | `/api/v1/suppression/check` | Check if HMAC exists in suppression ledger |
| `POST` | `/api/v1/suppression/restore-replay`| Run restore-replay check on target database |
| `POST` | `/api/v1/coverage/scan` | Trigger schema drift introspection scan |
| `GET` | `/api/v1/coverage/findings` | List coverage drift findings |
| `PATCH` | `/api/v1/coverage/findings/{id}` | Resolve coverage finding (MAPPED or NOT_PII) |
| `GET` | `/api/v1/coverage/health` | Compliance health check endpoint |
| `POST` | `/api/v1/deadlines/sweep` | Trigger deadline escalation check |

---

## 6. Testing & Claim-Proving Verification Suite (Step 3)

The MVP test suite (`pytest` + `pytest-asyncio`) validates all 12 architectural claims:

1. **Idempotency (`test_idempotency.py`):**
   - Run each step type (`DELETE`, `ANONYMIZE`, `CRYPTO_SHRED`) twice consecutively against test databases; verify identical end state and zero errors.
2. **Crash-Resume (`test_crash_resume.py`):**
   - Terminate worker mid-saga between Phase 2 steps; restart worker and verify it picks up exactly at the pending step without re-executing completed side-effects.
3. **Quarantine Compensation (`test_quarantine_cancel.py`):**
   - Submit cancellation request during quarantine window; verify data access is restored and request enters `CANCELLED`. Submit cancellation post-pivot; verify `409 Conflict` and forward execution continues.
4. **Subject-Recreation Race (`test_recreation_race.py`):**
   - Initiate erasure for `subject_1 (email_a)`. Register new `subject_2` with the same `email_a` after `cutoff_at`. Complete erasure; verify `subject_1` data is destroyed while `subject_2` data remains intact.
5. **Non-Reversible Anonymization (`test_anonymization.py`):**
   - Verify `HASH` strategy fails data map validation. Confirm anonymized rows cannot be matched against a rainbow dictionary of known candidate emails.
6. **Crypto-Shredding Verification (`test_crypto_shred.py`):**
   - Write ciphertext under subject DEK. Destroy subject DEK. Confirm ciphertext persists in database but decryption fails. Restore an older DB backup; verify data is still unreadable without the destroyed key.
7. **Restore-Replay (`test_restore_replay.py`):**
   - Erase subject in non-crypto store. Restore database snapshot containing original subject rows. Run restore-replay worker; verify subject rows are automatically re-erased.
8. **Verifier Disagreement (`test_verifier_disagreement.py`):**
   - Inject remnant row in downstream store after purge step completes. Confirm independent verifier fails the step, transitions request to `PARTIALLY_COMPLETED`, and halts certificate generation.
9. **Ledger Integrity & Immutability (`test_ledger_integrity.py`):**
   - Attempt direct SQL `UPDATE` or `DELETE` on `erasure_ledger`; verify PostgreSQL trigger aborts transaction. Manually corrupt a payload in test mode; verify `ledger/verify` detects broken hash chain.
10. **Zero-PII Proof (`test_zero_pii_audit.py`):**
    - Search full text of all ledger payloads, logs, and generated certificates for subject's email and plaintext name; assert 0 occurrences found.
11. **Coverage Detection (`test_coverage_monitor.py`):**
    - Create unmapped table `shadow_leads (id INT, user_email VARCHAR)`. Run coverage monitor; assert high-severity finding generated.
12. **Deadline Escalation (`test_deadlines.py`):**
    - Fast-forward virtual clock; confirm T-14 and T-7 warnings trigger, and expired requests enter `OVERDUE`. Verify deadline extensions require an explicit logged justification.

---

## 7. Delivery Schedule & Task Breakdown

| Sprint | Milestones | Key Deliverables |
|---|---|---|
| **Sprint 1** | Milestones 1 & 2 | Data model migrations, triggers, Data Map parser/validator, Connector interface, Postgres connector |
| **Sprint 2** | Milestones 3, 4 & 5 | Request intake & planner, Saga executor with quarantine/pivot, Envelope crypto-shredder |
| **Sprint 3** | Milestones 6 & 7 | Independent verifier, Hash-chained append-only ledger, Zero-PII certificate, Suppression ledger & restore-replay |
| **Sprint 4** | Milestones 8, 9 & 10 | Coverage drift monitor, Deadline escalations & legal holds, Webhook/manual connectors, Test suite completion |
