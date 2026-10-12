# ArchiveVault Test Suite & Architectural Claims Verification

ArchiveVault's MVP is backed by a 13-test automated verification suite (`pytest` + `pytest-asyncio` + `httpx`) designed to validate every invariant articulated in `docs/ARCHITECTURE.md` and `docs/CURSOR_CONTEXT.md`.

---

## 1. Architectural Claims Verification Matrix

| Claim | Test File | Architectural Guarantee Verified |
|---|---|---|
| **1. Idempotency** | `tests/test_idempotency.py` | Running any erasure step (`DELETE`, `ANONYMIZE`, `CRYPTO_SHRED`, or `VERIFY`) twice in succession produces identical end states and zero unexpected errors. |
| **2. Crash-Resume** | `tests/test_crash_resume.py` | Killing a worker mid-saga and restarting resumes execution strictly from the next pending step; already completed steps are never repeated. |
| **3. Quarantine Compensation** | `tests/test_quarantine_cancel.py` | Pre-pivot cancellation reverts soft-quarantines and completely restores user data access. Cancellation attempts after the pivot point are rejected with `409 Conflict`. |
| **4. Recreation Race** | `tests/test_recreation_race.py` | A user account recreated with the same email mid-saga survives intact because erasure locates records using stable internal UUIDs combined with a frozen `cutoff_at` timestamp. |
| **5. Anonymization Safety** | `tests/test_anonymization.py` | Strictly rejects `HASH` strategies during data map validation (hashing is pseudonymization, not anonymization under GDPR). Proves anonymized data cannot be linked via dictionary attacks. |
| **6. Envelope Crypto-Shred** | `tests/test_crypto_shred.py` | Destroying the subject's wrapped DEK leaves ciphertext unreadable across both active tables and historical snapshot backups. |
| **7. Restore-Replay Defense** | `tests/test_restore_replay.py` | When a database is restored from an older backup, the restore-replay background worker compares record HMACs against the suppression ledger and automatically re-erases resurrected data. |
| **8. Verifier Disagreement** | `tests/test_verifier_disagreement.py` | Independent read-scan catches remnant rows surviving erasure, keeps request state in `PARTIALLY_COMPLETED`, and halts certificate generation. |
| **9. Ledger Integrity** | `tests/test_ledger_integrity.py` | Append-only PostgreSQL triggers block direct SQL `UPDATE` and `DELETE`. Tampered payloads are immediately detected during cryptographic hash chain verification. |
| **10. Zero-PII Audit** | `tests/test_zero_pii_audit.py` | Full-text scan across all ledger payloads, evidence metadata, and generated compliance certificates confirms zero occurrences of raw personal identifiers (no emails or names). |
| **11. Coverage Drift Monitor** | `tests/test_coverage_monitor.py` | Active schema introspection detects unmapped shadow tables and columns with elevated severity (`CRITICAL`/`HIGH`) and fails compliance health until resolved. |
| **12. Deadline Escalation** | `tests/test_deadlines.py` | Virtual time sweeps trigger T-14 warnings and T-7 `AT_RISK` states. Overdue requests cannot be silently cleared and require logged lawful extensions (e.g. GDPR Art 12(3)). |
| **13. Full API Lifecycle** | `tests/test_api_flow.py` | Comprehensive end-to-end integration test exercising all 11 Postman collection folders through the FastAPI HTTP client. |

---

## 2. Running the Test Suite Locally

### Prerequisites
Ensure the primary ArchiveVault and downstream PostgreSQL containers are running:
```bash
docker-compose up -d
```

Initialize schemas and immutability triggers:
```bash
PYTHONPATH=. .venv/bin/python -m src.init_db
```

### Run All Tests
```bash
PYTHONPATH=. .venv/bin/pytest -v
```

### Run a Specific Test
```bash
PYTHONPATH=. .venv/bin/pytest tests/test_crypto_shred.py -v
```

---

## 3. Postman API Collection Testing

A pre-configured Postman test collection is provided in `docs/archive_vault.postman_collection.json`.

To run via Newman CLI:
```bash
newman run docs/archive_vault.postman_collection.json --env-var "baseUrl=http://localhost:8000"
```
