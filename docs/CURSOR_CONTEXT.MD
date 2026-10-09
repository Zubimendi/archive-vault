# Agent context: building ArchiveVault from scratch

Read `docs/ARCHITECTURE.md` fully first. It is the spec; sections §3
(saga semantics), §5 (crypto-shredding caveats), §6 (anonymization), and
§7 (the proof paradox) are the ones most likely to get quietly
"simplified" into something wrong. Don't.

## Step 0: infra (set up yourself, not included)
Python 3.12 project (FastAPI + SQLAlchemy 2 async + asyncpg, matching
VaultKeep/SearchCraft conventions), `docker-compose.yml` with Postgres
(plus a second Postgres container to act as a "downstream app database"
for testing connectors), `pytest` + `pytest-asyncio`, `cryptography`.
QueueLine must be reachable for step execution. Hand-written SQL
migrations, as in prior projects.

## Step 1: data model (tenant-scoped everywhere)
- `tenants` (id, regime_profile, kek_ref)
- `subjects` (id [opaque UUID], tenant_id, wrapped_dek, status
  ACTIVE|ERASED, created_at). NO plaintext identifiers stored here.
- `data_map_versions` (id, tenant_id, version, document jsonb, status).
  Immutable once ACTIVE (trigger, same pattern as FormFlow versions).
- `erasure_requests` (id, tenant_id, subject_id, trigger, regime,
  data_map_version_id, received_at, due_at, cutoff_at, quarantine_until,
  state)
- `erasure_steps` (id, request_id, store_id, dataset, phase
  QUARANTINE|PURGE|PROPAGATE, action, state, attempts, evidence jsonb,
  last_error). Row-lock on state transitions.
- `suppression_ledger` (subject_hmac, erased_at, request_id). HMAC with
  a pepper from env/secret store, never a plain hash.
- `erasure_ledger` (id, seq, request_id, event_type, payload jsonb,
  prev_hash, hash). Append-only trigger + hash chain. Payloads carry NO
  field values or direct identifiers.
- `legal_holds` (id, tenant_id, subject_id, reason, placed_by,
  expires_at, released_at)
- `coverage_findings` (id, store_id, object_name, column_name, finding
  type, severity, detected_at, resolved_at, resolution, reviewer)

## Step 2: build in this order
1. **Data map loader + validator.** Parse/validate the YAML-shaped
   document; reject unsupported anonymization strategies (only NULLIFY,
   random replacement, generalization). Version immutability trigger.
2. **Connector interface** (`locate`, `erase`, `verify`) and the
   `postgres` connector against the second test database. Build `verify`
   alongside `erase`, not afterward.
3. **Request intake + planner.** Dedupe open requests per subject,
   compute `due_at` from the regime profile, freeze `cutoff_at`, expand
   the data map into ordered `erasure_steps` across the three phases.
4. **Saga executor.** State persisted before execution; steps run via
   QueueLine; quarantine → pivot → purge/propagate transitions through
   one explicit transition table. Retry with backoff in the retriable
   phases; cancellation only legal before the pivot.
5. **Envelope encryption for `CRYPTO_SHRED` datasets.** Per-subject DEK
   wrapped by the tenant KEK (reuse VaultKeep's AES-GCM approach).
   Erasure = delete the wrapped DEK; `verify` = attempt decrypt, expect
   failure.
6. **Verifier + erasure ledger + certificate.** Independent read path
   that re-scans; hash-chained ledger writes on every transition;
   certificate generator that is tested to contain no PII.
7. **Suppression ledger + restore-replay job.** Given a restored
   database, re-erase any suppressed subject whose data reappeared.
8. **Coverage monitor.** Introspect `information_schema`, diff against
   the active data map, apply name heuristics and FK-into-subject-table
   detection, persist findings, expose an unresolved-findings health
   check.
9. **Deadline escalation job and legal holds.** T-14/T-7 notifications,
   AT_RISK/OVERDUE states, hold placement/release with ledger events.
10. **Connectors `webhook` and `manual`.** Webhook delivery through
    Dispatcher/QueueLine; manual tasks require evidence before
    completion.

## Step 3: tests that prove the claims
- **Idempotency:** run every step type twice; end state identical, no
  errors.
- **Crash-resume:** kill the executor mid-saga; on restart it continues
  from persisted state and never repeats a completed step's side effect
  incorrectly.
- **Quarantine compensation:** cancel before the pivot; access fully
  restored. Attempt cancel after the pivot; rejected.
- **Subject-recreation race:** start erasure, recreate a subject with
  the same email mid-saga; the new record survives (stable-key and
  cutoff logic).
- **Anonymization is non-reversible:** a test that tries to re-link an
  anonymized row via a dictionary of known emails and fails; a test that
  rejects a configured `HASH` strategy.
- **Crypto-shred:** ciphertext persists, decrypt fails after DEK
  deletion. Restore an old snapshot of the encrypted table; still
  unreadable.
- **Restore replay (non-crypto store):** restore plaintext snapshot,
  run replay, subject re-erased.
- **Verifier disagreement:** plant a remnant row the eraser didn't know
  about; verifier fails the request (state stays out of COMPLETED).
- **Ledger integrity:** tamper with a historical ledger payload;
  chain verification detects it. Direct UPDATE/DELETE rejected by the
  trigger.
- **No-PII proof:** scan the ledger and certificates for the test
  subject's email/name; zero matches.
- **Coverage detection:** add an unmapped table with an `email` column;
  a finding appears with elevated severity.
- **Deadlines:** simulate time; escalation fires at T-14/T-7, OVERDUE
  can't be silently cleared, extension requires reason + logged
  notification.

## Design decisions already made (don't relitigate)
1. Four distinct actions (DELETE / ANONYMIZE / CRYPTO_SHRED /
   RETAIN_WITH_REASON); never conflate them.
2. Saga is quarantine (compensable) → pivot → forward-only; no
   compensating deletes.
3. Per-subject DEKs under per-tenant KEKs, not per-tenant only.
4. Anonymization never uses hashing; HMAC only in the suppression ledger.
5. The ledger and certificates contain no direct identifiers or values.
6. Locate subjects by stable internal key + `cutoff_at`, never by email
   alone.
7. One pipeline for all three triggers.
8. Execution via QueueLine and vendor delivery via Dispatcher/QueueLine:
   do not rebuild either.