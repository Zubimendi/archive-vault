# ArchiveVault

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue.svg)](https://www.postgresql.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/Tests-13%20Passed-brightgreen.svg)]()

A production-grade GDPR/CCPA data lifecycle and erasure service, built in Python. Project 22 of a 15-week backend roadmap. 

The problem: *"Delete everything about this user, everywhere, and be able to prove it."* — a legal compliance requirement that most software organizations manage with a spreadsheet and a prayer.

ArchiveVault treats data erasure not as a naive batch of scattered SQL `DELETE` queries, but as a distributed systems challenge governed by honest state machines, cryptographic guarantees, and automated drift monitoring.

**Status: MVP Development Concluded & Validated.**

---

## Architectural Core: The Four Pillars

ArchiveVault is designed around four foundational invariants documented in `docs/ARCHITECTURE.md`:

### 1. Retention Policy as Declarative Config
Personal data locations and destruction rules are defined in a versioned, immutable **Data Map** document (YAML/JSON) rather than hardcoded into service layers.
- Four distinct, mutually exclusive actions: `DELETE`, `ANONYMIZE`, `CRYPTO_SHRED`, and `RETAIN_WITH_REASON`.
- Strictly validates anonymization strategies: only `NULLIFY`, `RANDOM`, and `GENERALIZE` are allowed. **`HASH` strategies are rejected at upload** because hashing produces pseudonymized data, not anonymized data under GDPR Article 17.
- Database triggers enforce immutability: once a data map version transitions to `ACTIVE`, it cannot be modified or deleted.

### 2. Erasure as a 3-Phase Saga with Honest Semantics
Erasure requests are a known vector for denial-of-service and account hijacking. ArchiveVault handles them as a directional saga:
1. **Phase 1: `QUARANTINE` (Compensable):** Soft-locks access to subject records while verifying request authenticity during the statutory cancellation window. Pre-pivot cancellation fully reverts quarantine and restores user access.
2. **The Pivot Point (Point of No Return):** Once the quarantine window expires or is administratively pivoted to `PURGING`, the saga becomes strictly forward-only and irreversible. Cancellation attempts post-pivot are rejected with `409 Conflict`.
3. **Phase 2: `PURGE` (Forward-Only Retriable):** Atomic batch operations against downstream stores (`DELETE`, `ANONYMIZE`, `CRYPTO_SHRED`).
4. **Phase 3: `PROPAGATE` (Outbound Delivery):** Dispatches signed deletion payloads to third-party processors and records operator evidence for offline stores.

### 3. True Envelope Crypto-Shredding
For high-volume append-only datasets (e.g., event logs, analytics tables) where rewriting immutable blocks or backups is impractical:
- **Key Hierarchy:** A tenant Key Encryption Key (KEK) wraps unique per-subject Data Encryption Keys (DEKs) using AES-256-GCM.
- **Instant Erasure:** Erasure physically zeroizes and deletes the subject's wrapped DEK from the key registry.
- **Backup Immunity:** Ciphertext remains in place, but destroying the DEK renders all current database records and historical snapshot backups permanently unreadable.

### 4. Resolving the "Proof Paradox" (Zero-PII Proofs)
Under GDPR, companies must prove they erased a subject's data, but compliance logs cannot retain the personal data they were ordered to delete:
- **Opaque Subject Identifiers:** Subjects are identified exclusively via internal UUIDs coupled with a frozen `cutoff_at` timestamp, eliminating subject-recreation race conditions.
- **Append-Only Tamper-Evident Ledger:** Every saga transition appends a block to a SHA-256 hash chain protected by PostgreSQL triggers against `UPDATE` and `DELETE`. Payloads are strictly sanitized to strip all PII.
- **Zero-PII Compliance Certificates:** Generates cryptographic verification certificates referencing ledger root hashes and action digests without storing email addresses, names, or identifiable values.

---

## Additional Systems

- **Schema Drift & Coverage Monitor:** Introspects target database schemas (`information_schema.columns` & `tables`), diffs against the active Data Map, and flags unmapped tables or suspect PII columns (matching `email`, `phone`, `ssn`, `dob`, `password`) as elevated `CRITICAL`/`HIGH` compliance findings.
- **Suppression Ledger & Restore-Replay Defense:** Computes keyed `HMAC-SHA256(pepper, identifier)` stored in a suppression ledger. When an older database backup is restored, a background replay worker detects resurrected records and automatically re-erases them.
- **Deadlines & Legal Holds Engine:** Manages statutory clocks (30 days for GDPR, 45 days for CCPA), triggers T-14 and T-7 `AT_RISK` escalation warnings, halts requests under litigation holds, and logs lawful complexity extensions (e.g., GDPR Art. 12(3)).

---

## System Architecture

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
                      │   - Suppression Ledger (HMAC with Pepper)     │
                      └──────────────┬────────────────┬───────────────┘
                                     │                │
            ┌────────────────────────▼──┐          ┌──▼─────────────────────────┐
            │   Saga Executor Engine    │          │  Background Schedulers     │
            │   - Row lock transitions  │          │  - Deadline Escalation     │
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

## Project Structure

```
archive-vault/
├── docker-compose.yml          # Dual PostgreSQL setup (ArchiveVault core + downstream app DB)
├── requirements.txt            # Python dependencies
├── .gitignore                  # Git tracking rules
├── README.MD                   # Project overview & operational guide
├── docs/
│   ├── ARCHITECTURE.MD         # Specification & system design
│   ├── CURSOR_CONTEXT.MD       # Engineering milestones & design decisions
│   ├── IMPLEMENTATION_PLAN.MD  # Sprint breakdown & architectural invariants
│   ├── STORY.MD                # Engineering narrative & interview talking points
│   ├── TESTING.MD              # Test suite verification guide
│   └── archive_vault.postman_collection.json # 11-folder Postman API test suite
├── src/
│   ├── config.py               # Application settings & secrets
│   ├── database.py             # SQLAlchemy 2.0 async engine & sessionmaker
│   ├── init_db.py              # Schema generation & PostgreSQL trigger installations
│   ├── main.py                 # FastAPI application & router montage
│   ├── api/
│   │   ├── dependencies.py     # Tenant extraction & session injection
│   │   ├── schemas.py          # Pydantic request/response schemas
│   │   └── routers/            # Health, Tenants, Data Maps, Subjects, Requests,
│   │                           # Ledger, Holds, Suppression, Coverage, Deadlines
│   ├── connectors/             # Postgres, Webhook, and Manual target store drivers
│   ├── crypto/                 # AES-GCM envelope encryption & suppression HMAC
│   ├── data_map/               # Data map loader, Pydantic parser & validation logic
│   ├── models/                 # SQLAlchemy ORM models (Tenant, Subject, Step, Ledger, etc.)
│   └── services/               # Core business services (Planner, Saga, Verifier, etc.)
└── tests/                      # Automated 13-test claim verification suite
```

---

## Getting Started

### 1. Start Infrastructure via Docker Compose
ArchiveVault requires two PostgreSQL instances: one for ArchiveVault core and a second acting as the downstream target application database:
```bash
docker-compose up -d
```
- ArchiveVault DB: `localhost:5434`
- Downstream App DB: `localhost:5435`

### 2. Environment Setup
Create a virtual environment and install dependencies:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Initialize Schemas & Immutability Triggers
Run the database initialization script to create tables and triggers:
```bash
PYTHONPATH=. .venv/bin/python -m src.init_db
```

### 4. Run the API Server
Start the FastAPI server via Uvicorn:
```bash
PYTHONPATH=. .venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```
Interactive API documentation is accessible at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## API Capabilities

ArchiveVault exposes a tenant-isolated REST API categorized into 11 functional domains:

| Category | Endpoint | Method | Purpose |
|---|---|---|---|
| **Health** | `/health` | `GET` | Service liveness probe |
| **Coverage** | `/api/v1/coverage/health` | `GET` | Compliance & unresolved drift health check |
| **Tenants** | `/api/v1/tenants` | `POST` | Onboard new tenant with KEK ref |
| | `/api/v1/tenants/{id}` | `GET` | Retrieve tenant compliance profile |
| | `/api/v1/tenants/{id}/offboard` | `POST` | Offboard tenant and shred all wrapped keys |
| **Data Maps** | `/api/v1/data-maps` | `POST` | Upload and validate declarative data map |
| | `/api/v1/data-maps/{id}/publish` | `POST` | Publish data map version (makes ACTIVE & immutable) |
| | `/api/v1/data-maps/active` | `GET` | Fetch active data map |
| **Subjects** | `/api/v1/subjects` | `POST` | Register subject with wrapped DEK (Zero-PII stored) |
| | `/api/v1/subjects/{id}` | `GET` | Inspect subject key status |
| **Requests** | `/api/v1/erasure-requests` | `POST` | Intake erasure request & expand saga steps |
| | `/api/v1/erasure-requests/{id}` | `GET` | Inspect request timeline and saga step states |
| | `/api/v1/erasure-requests/{id}/cancel`| `POST` | Pre-pivot quarantine cancellation |
| | `/api/v1/erasure-requests/{id}/pivot` | `POST` | Force pivot to purge (irreversible point of no return) |
| | `/api/v1/erasure-requests/{id}/execute-step` | `POST` | Execute next pending saga step |
| | `/api/v1/erasure-requests/{id}/verify`| `POST` | Trigger independent verifier re-scan |
| | `/api/v1/erasure-requests/{id}/certificate` | `GET` | Generate Zero-PII compliance certificate |
| | `/api/v1/erasure-requests/{id}/extend`| `POST` | Lawful deadline extension under GDPR Art 12(3) |
| **Ledger** | `/api/v1/ledger` | `GET` | Inspect append-only ledger events |
| | `/api/v1/ledger/verify` | `GET` | Cryptographic hash chain verification |
| **Legal Holds**| `/api/v1/holds` | `POST` | Place legal hold (freezes in-flight requests into `HELD`) |
| | `/api/v1/holds` | `GET` | List active legal holds |
| | `/api/v1/holds/{id}/release` | `POST` | Release legal hold and resume saga |
| **Suppression**| `/api/v1/suppression/check` | `POST` | Query suppression ledger HMAC |
| | `/api/v1/suppression/restore-replay` | `POST` | Run restore-replay reconciliation sweep |
| **Coverage** | `/api/v1/coverage/scan` | `POST` | Trigger schema drift introspection |
| | `/api/v1/coverage/findings` | `GET` | List unmapped columns/tables |
| | `/api/v1/coverage/findings/{id}` | `PATCH` | Lawfully resolve coverage finding |
| **Deadlines** | `/api/v1/deadlines/sweep` | `POST` | Trigger daily T-14/T-7 deadline sweep |

---

## Verification & Claim Tests

ArchiveVault includes a comprehensive automated test suite validating all 12 architectural claims:

```bash
PYTHONPATH=. .venv/bin/pytest -v
```

```
tests/test_idempotency.py::test_erasure_steps_idempotency                         PASSED
tests/test_crash_resume.py::test_crash_resume_mid_saga                            PASSED
tests/test_quarantine_cancel.py::test_quarantine_compensation_and_pivot_irreversibility PASSED
tests/test_recreation_race.py::test_subject_recreation_race                      PASSED
tests/test_anonymization.py::test_anonymization_non_reversibility_and_hash_rejection PASSED
tests/test_crypto_shred.py::test_crypto_shredding_and_backup_unreadability       PASSED
tests/test_restore_replay.py::test_restore_replay_re_erasure                     PASSED
tests/test_verifier_disagreement.py::test_verifier_disagreement_halts_completion PASSED
tests/test_ledger_integrity.py::test_ledger_immutability_and_chain_verification  PASSED
tests/test_zero_pii_audit.py::test_zero_pii_audit_in_ledger_and_certificates     PASSED
tests/test_coverage_monitor.py::test_coverage_drift_detection_and_resolution     PASSED
tests/test_deadlines.py::test_deadlines_escalation_and_lawful_extension          PASSED
tests/test_api_flow.py::test_full_api_workflow                                    PASSED

============================== 13 passed in 1.27s ===============================
```

See [docs/TESTING.MD](file:///home/francisco4/Desktop/Kali%20Projects/archive-vault/docs/TESTING.MD) for full details on test assertions.

---

## Postman Collection

A complete Postman test collection with 25 pre-written integration tests across all 11 suites is located at:
`docs/archive_vault.postman_collection.json`

Import into Postman or run via Newman CLI:
```bash
newman run docs/archive_vault.postman_collection.json --env-var "baseUrl=http://localhost:8000"
```

---

## License

This project is licensed under the MIT License. Engineering design and architecture only; not legal advice.