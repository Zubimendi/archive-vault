# Architecture: principles → design

> Engineering design document, not legal advice. Statutory deadlines,
> exemptions, and what regulators accept as "erasure" vary by jurisdiction
> and change over time. Treat every legal-adjacent value here as a
> configurable default to confirm with counsel, not a fact to hardcode.

## System shape

```
 erasure request ─┐
 (data subject)   ├─▶ ┌──────────────────────┐
 retention expiry ─┤   │ request intake         │  dedupe, assign deadline,
 (scheduled sweep) │   │ + planner               │  freeze cutoff timestamp,
 tenant offboard ──┘   └──────────┬───────────┘  resolve plan from DATA MAP
                                   ▼
                      ┌──────────────────────┐
                      │ declarative data map    │  stores → datasets →
                      │ (config, not code)      │  subject key + action +
                      └──────────┬───────────┘  retention + legal basis
                                   ▼
        ┌──────────────── erasure saga (persisted state) ─────────────────┐
        │ Phase 1 QUARANTINE   →   Phase 2 PURGE    →   Phase 3 PROPAGATE  │
        │ compensable steps       PIVOT (point of      retriable steps     │
        │ (cancel = restore)      no return)           (forward-only)      │
        └─────────────────────────────┬──────────────────────────────────┘
                                       ▼
          ┌────────────────┐   ┌─────────────────┐   ┌──────────────────┐
          │ verifier         │   │ erasure ledger     │   │ coverage monitor   │
          │ (re-scan stores, │   │ (hash-chained,     │   │ (schema drift vs.  │
          │ expect zero)     │   │ holds NO subject   │   │ the data map)      │
          └────────────────┘   │ PII)               │   └──────────────────┘
                                 └─────────────────┘
```

---

## 0. Framing: what "erased" actually means

Four different actions all get called "deletion," and conflating them is
the most common design error in this space. ArchiveVault treats them as
distinct, explicit **actions** a dataset can be configured with:

| Action | What it does | When it's right |
|---|---|---|
| `DELETE` | Physically removes the rows | Data with no ongoing need |
| `ANONYMIZE` | Irreversibly strips identifying fields, keeps the row | Rows needed for aggregates/integrity (e.g. an order row whose FK must survive) |
| `CRYPTO_SHRED` | Destroys the key that encrypts the data | Data that is copied into places you can't surgically edit (backups, replicas, archives) |
| `RETAIN_WITH_REASON` | Keeps data, records the legal basis and an expiry | Legal obligation or legal-claims exemptions (e.g. invoices kept for tax law) |

Two legal "clocks" frame the design: the **response deadline** for a
subject request (GDPR: one month, extendable by up to two further months
for complex or numerous requests, with notice to the subject; CCPA/CPRA:
45 days, extendable once by another 45), and the **storage limitation
principle** (data must not be kept longer than necessary, which
implies automatic deletion at retention expiry even when nobody asked).
Both are configuration, not constants (see §10).

---

## 1. Retention policy as declarative config, not scattered DELETE statements

**The failure mode this prevents:** erasure logic implemented as a
`DELETE FROM ...` sprinkled across services, written by whoever built
each feature. Nobody can answer "where does personal data live?" because
the answer is distributed across a dozen codebases and nobody wrote it
down. A new feature ships a new table with email addresses in it, and
the erasure path silently doesn't know it exists.

**The mechanism:** a central, versioned **data map**: a declarative
registry of every store, every dataset inside it that holds personal
data, how to find one subject's records, and what to do with them.

```yaml
stores:
  - id: app-postgres
    connector: postgres
    datasets:
      - name: users
        subject_key: id
        action: ANONYMIZE
        anonymize: { email: NULLIFY, full_name: NULLIFY, phone: NULLIFY }
      - name: support_messages
        subject_key: author_id
        action: DELETE
      - name: invoices
        subject_key: customer_id
        action: RETAIN_WITH_REASON
        retention: { days: 2555, basis: "tax record-keeping obligation" }
        on_expiry: DELETE
  - id: analytics-warehouse
    connector: postgres
    datasets:
      - name: events
        subject_key: user_id
        action: CRYPTO_SHRED          # payload column encrypted under subject DEK
  - id: email-vendor
    connector: webhook                # third-party processor, see §9
    datasets:
      - name: contacts
        subject_key: email
        action: DELETE
```

Because policy is data, it can be reviewed by a compliance officer who
can't read Go or Python, diffed in version control, validated by tooling
(§8), and changed without a code deploy. Each data-map version is
immutable once active, and every erasure request records which
version it executed under (the same "published versions are immutable,
in-flight work pins the version it started with" discipline as
FormFlow's versioned form definitions).

---

## 2. Two triggers, one pipeline

Erasure is initiated three ways: a **subject request**, a **retention-
expiry sweep** (scheduled job finds datasets whose retention window has
elapsed), and **tenant offboarding**. All three produce the same
`ErasureRequest` record and run through the identical planner, saga, and
verifier. There is no "automatic retention deletion path" separate from
the "user requested deletion path," because two code paths performing the
same destructive operation will eventually diverge in behavior, and the
path that gets less scrutiny (the scheduled one nobody watches) is the
one that ends up with the bug. This is the same "backfill goes through
the normal path, not a special mode" lesson from PyDataRex, applied to
deletion.

---

## 3. Erasure as a saga, with an honest answer about "compensation"

Cascading deletion across multiple independent stores is a distributed
transaction problem with no shared transaction manager, so it's modeled
as a **saga**: a sequence of local steps, each with persisted state. But
the standard saga story ("if step N fails, run compensating steps for
1..N-1") does **not** fit erasure, and pretending it does would be the
wrong design.

**Why compensation mostly doesn't apply:** the legal obligation is to
*complete* erasure. A half-erased subject isn't rolled back to fully-
present; it's driven forward to fully-erased. And a physical `DELETE`
can't be compensated anyway. So ArchiveVault uses the three-category
saga model (compensable transactions → pivot transaction → retriable
transactions):

1. **Phase 1, QUARANTINE (compensable).** Reversible steps that make the
   subject's data inaccessible without destroying it: revoke sessions
   and API keys, flag records as quarantined so application reads
   exclude them, suspend processing. This phase exists for a real
   reason: **erasure requests are an attack vector**. An attacker who
   can submit a forged request (or compromise a support agent's
   session) can destroy a victim's account irreversibly. A short,
   configurable quarantine window (days, well inside the statutory
   deadline) gives the request a chance to be cancelled, and
   cancellation is a genuine compensation: lift quarantine, restore
   access.
2. **The pivot.** After the quarantine window elapses with no
   cancellation, the request crosses the **point of no return**. The
   pivot transition is explicit, recorded in the ledger (§7), and from
   here nothing is rolled back.
3. **Phase 2, PURGE and Phase 3, PROPAGATE (retriable).** Destructive
   steps (`DELETE`, `ANONYMIZE`, `CRYPTO_SHRED`) and downstream
   notifications to processors. These are **forward-only**: failures
   retry with backoff indefinitely rather than reverting, and the
   request cannot be marked `COMPLETED` until every step is verified.
   A request with some steps done and others failing is `PARTIALLY_
   COMPLETED`, loudly visible, and escalates as its deadline approaches.

```
RECEIVED → PLANNED → QUARANTINED ─(window elapses)→ PURGING → PROPAGATING → VERIFYING → COMPLETED
               │          │                              │           │            │
               │          └─(cancel)→ CANCELLED          └───────────┴──(failure)─┴→ PARTIALLY_COMPLETED
               └─(legal hold placed)→ HELD ──(released)→ PLANNED
```

---

## 4. Idempotency, resumability, and the subject-recreation race

**Erasure is naturally idempotent, so lean on that.** Deleting
already-deleted rows is a no-op; anonymizing already-anonymized fields
is a no-op. Every step is written to be safe to run any number of times,
which is what makes retry-until-done correct instead of dangerous (the
same "at-least-once execution requires idempotent work" contract
documented for PyDataRex tasks).

**Resumability:** saga state (request state, per-step state, attempt
counts) is persisted in Postgres before any step executes, so a worker
crash mid-erasure resumes from recorded state instead of restarting or
forgetting. Step execution is handed to QueueLine (Project 1), whose
lease/fencing-token mechanics already prevent a stalled worker from
corrupting a step another worker has since picked up. ArchiveVault does
not rebuild a job queue.

**The subject-recreation race (a bug that only appears in production):**
a user requests erasure, then signs up again with the same email while
the saga is still running. If steps locate the subject by *email*, a
late-running purge step deletes the **new** account. Two defenses, both
required:

- Locate subjects by **stable internal key** (the user's immutable ID),
  never by a mutable attribute, wherever the store allows it.
- Freeze an `erasure_cutoff_at` timestamp at request intake. Steps
  against stores that can't key by stable ID (e.g. a vendor keyed by
  email) only touch records created at or before the cutoff. Anything
  newer belongs to a new relationship and is not erased.

---

## 5. Crypto-shredding: per-subject keys, per-tenant wrapping

**The problem it solves that nothing else does cleanly:** *backups,
replicas, and archives.* You cannot surgically delete one person's rows
from a month of immutable snapshots, an object-store archive, and a
standby replica. "Delete from production and wait for backups to age
out" is the common fallback, and it leaves the data recoverable for the
whole rotation window. Crypto-shredding inverts the approach: the
subject's data is encrypted at write time under a key that belongs to
that subject; erasure destroys the **key**, and every ciphertext copy
everywhere becomes permanently unreadable at once, with no need to find
and touch each copy.

**Key hierarchy** (envelope encryption, the same mechanism as VaultKeep,
Project 4, recognized and reused):

```
 per-tenant KEK  (wraps subject DEKs; destroying it = tenant-level shred,
      │            used for tenant offboarding)
      └── per-subject DEK  (encrypts that subject's data;
                            destroying it = single-subject erasure)
```

Per-subject granularity is what makes individual erasure possible;
per-tenant wrapping is what makes tenant offboarding a single operation
and keeps DEKs at rest protected.

**Honest caveats, because this technique is routinely oversold:**

- **It only covers data actually encrypted under the subject's DEK.**
  Plaintext copies (application logs, analytics exports, search indexes,
  caches, derived tables) are untouched. The data map (§1) must classify
  every location and route plaintext ones to `DELETE`/`ANONYMIZE`;
  `CRYPTO_SHRED` is not a blanket answer.
- **The key store's own backups are a circularity trap.** If a backup of
  the key store retains the destroyed DEK for a year, the data was
  recoverable for a year. Key-store backup retention must be explicitly
  bounded and short, and the erasure certificate (§7) should state the
  date after which the key is unrecoverable by *any* copy, not claim
  instant irreversibility.
- **"Unrecoverable" is relative to a threat model.** Ciphertext persists.
  If a key leaked before destruction, or the cipher is later broken,
  the data is exposed. Crypto-shredding gives strong practical
  irreversibility, not a mathematical absolute.
- **Regulatory acceptance is practice, not statute.** It is widely used
  and generally defensible as effective erasure when done rigorously,
  but it is not a codified definition; record the reasoning in the
  compliance file rather than assuming it.

---

## 6. Anonymization done correctly

`ANONYMIZE` must be **irreversible and genuinely non-identifying**, and
this is where well-meaning implementations quietly fail. Replacing an
email with `SHA256(email)` is **pseudonymization, not anonymization**:
anyone with a candidate email list can re-derive the hash and re-link the
record, and pseudonymized data is still personal data under GDPR.
ArchiveVault's allowed anonymization strategies are therefore limited
to `NULLIFY`, replacement with fresh random values that carry no
derivation from the original, and deliberate generalization (e.g. exact
birth date to year). A keyed hash is permitted only for the suppression
ledger (§7), where re-linkage is the explicit purpose and its status as
pseudonymous data is acknowledged and bounded, never as an "anonymization"
strategy.

---

## 7. Proof of erasure, and the paradox it creates

"Provably" is the product. Three mechanisms:

**Verification, not assumption.** After the purge steps, an independent
verifier re-queries every dataset for the subject's key and expects zero
matches (and for `CRYPTO_SHRED`, attempts a decrypt and expects failure).
A step's own "rows affected: 12" return value proves the delete ran, not
that nothing remains (triggers, replicas, or a second table can leave
remnants); the verifier is a separate read path whose job is to disagree
if reality does. This is the same "don't trust the writer to grade its
own work" principle behind LedgerLine's reconciler and SearchCraft's
drift check.

**A tamper-evident ledger.** Every state transition (received, planned,
quarantined, pivoted, step completed, verified, certificate issued) is
appended to a **hash-chained, append-only** log (each entry's hash covers
the previous entry's hash, enforced append-only by a database trigger),
the exact technique from DocSign (Project 19) applied to compliance
events, with the same honest caveat: it makes undetected editing hard,
not impossible for a privileged DB administrator who rewrites the chain
forward; periodically anchoring the latest hash outside the database
(a separate write-once store, or a published digest) is the
defense-in-depth step.

**The paradox: the proof must not retain the data it proves was deleted.**
An audit log containing "erased user jane@example.com" is a new, durable
store of exactly the personal data you were asked to destroy. So:

- The ledger references subjects only by an **opaque internal subject
  ID**, never by email, name, or other direct identifiers.
- Payloads never contain field values, only counts, dataset names, and
  verification outcomes.
- A minimal **suppression ledger** stores `HMAC(pepper, normalized
  identifier)` plus the erasure timestamp. This record has a specific,
  documented purpose, **restore replay**: after any backup restore or
  re-import, the suppression ledger is consulted to re-erase subjects
  whose data has reappeared from an older snapshot. It is pseudonymous
  data retained for accountability and to make erasure stick; its
  retention is bounded and its legal rationale recorded. (With
  `CRYPTO_SHRED`, restore replay is automatic, because the key is gone,
  which is another reason that action is preferred where it fits.)
- The **erasure certificate** issued to the requester states what was
  done and when, contains no personal data, and notes any retained data
  with its legal basis and expiry.

---

## 8. Coverage is a monitored property, not an assumption

The single most common real-world erasure failure isn't a bug in the
deletion logic; it's **data nobody mapped.** A developer adds
`profile_photos` or an `ip_address` column, nobody updates the data map,
and every subsequent erasure request is silently incomplete. Because the
data map's correctness decays the moment engineering ships anything,
ArchiveVault continuously checks coverage:

- **Schema drift detection:** a scheduled job introspects each connected
  store (`information_schema` for SQL stores) and diffs it against the
  data map. Any table or column absent from the map is a **coverage
  finding**.
- **Heuristic flags:** unmapped columns whose names match identifier
  patterns (`email`, `phone`, `ip`, `address`, `dob`, `name`), and
  unmapped tables with a foreign key into a mapped subject table, are
  raised with higher severity.
- Findings must be resolved explicitly: mapped, or marked
  `NOT_PERSONAL_DATA` with a reviewer and a reason. An unresolved
  finding older than a threshold fails a compliance health check.

This reframes the hardest part of GDPR engineering, *knowing where the
data is*, as an alerting problem with an owner, rather than a one-time
audit that's out of date within a month.

---

## 9. Third-party processors via connectors, not bespoke integrations

Data held by processors (email platforms, analytics vendors, support
tools) must be erased too, and the controller is obliged to notify
recipients of erasure. Each store sits behind a **connector** exposing
the same three operations: `locate(subject)`, `erase(subject)`,
`verify(subject)`. The built-in connectors: `postgres` (direct SQL),
`webhook` (HTTP call to a vendor API, delivered through Dispatcher/
QueueLine for retries and signing rather than reimplemented), and
`manual` (a human task with an evidence upload requirement, for
processors with no API). A step is only `COMPLETED` when its `verify` has
passed, or, for `webhook`/`manual`, when an acknowledgment is recorded.
Vendors that silently drop the request are exactly why verification and
the deadline escalation (§10) exist. This is the strategy-pattern
discipline used for NotifyHub's providers and GateKeeper's algorithms,
here applied to heterogeneous data stores.

---

## 10. Deadlines as first-class data

Every request carries `received_at`, `due_at` (computed from a per-
tenant **regime profile**: GDPR, CCPA, or custom, with deadline days and
extension rules as config), and a status. A scheduled job escalates:
notifications at T-14 and T-7 days, an `AT_RISK` flag when any step is
still retrying inside the final window, and an `OVERDUE` state that
cannot be silently cleared. Extension handling (the lawful extension for
complex requests) is an explicit, logged action with a required reason
and a record that the subject was notified, not a quiet edit to `due_at`.
The ledger makes any such change visible.

---

## 11. Retention exceptions and legal holds

Erasure is not absolute: obligations such as tax record-keeping, and the
need to establish or defend legal claims, can lawfully override a
request for specific data. ArchiveVault models this explicitly rather
than as an operator "just skipping" a table:

- `RETAIN_WITH_REASON` datasets record the basis and retention period;
  the planner marks that step `RETAINED` with the reason (surfaced on the
  certificate), and schedules a deferred erasure at retention expiry
  using the normal pipeline (§2).
- A **legal hold** (a litigation hold on a subject or dataset) freezes
  erasure: the request moves to `HELD`, resumes when the hold is
  released, and the deadline interaction is recorded. Placing and
  releasing a hold requires an authorized role and writes ledger events.

---

## 12. Multi-tenancy

Every record, policy, key, and ledger entry is tenant-scoped at the query
level, never post-filtered. Each tenant has its own KEK (so one tenant's
key compromise or offboarding shred cannot touch another's data), its own
regime profile, and its own data-map lineage. Tenant offboarding is just
another trigger (§2) whose plan is "purge everything, then destroy the
tenant KEK."

---

## 13. Observability

Metrics map to what a compliance owner and on-call engineer actually need:
requests by state, time-to-complete distribution, **requests within N days
of their deadline and not completed** (the alert that matters most),
failing steps by connector, unresolved coverage findings, verifier
disagreements (a verifier failure after a "successful" purge is an
incident, not a metric), and ledger-chain verification status.

---

## 14. Known limitations (stated, not hidden)

- **Derived data and ML models.** Aggregates are generally fine;
  models trained on personal data are an open research problem
  (machine unlearning). Not solved here; flagged as residual risk.
- **Unstructured data** (free-text fields, documents, screenshots)
  isn't discoverable by schema introspection; mapping it relies on
  humans classifying where it lives.
- **Crypto-shredding scope** is only as complete as the set of data
  actually encrypted under subject keys (§5).
- **Identity verification of the requester is an input, not a feature.**
  A forged request is mitigated by the quarantine window (§3), not
  prevented by ArchiveVault.

## 15. Out of scope

- Verifying that the requester is the data subject (assumed done upstream).
- Right-of-access and portability requests (a natural extension: the same
  data map's `locate()` already knows where everything is).
- Consent management, cookie banners, cross-border transfer mechanisms.
- Automated PII discovery in unstructured content (ML classification).
- Legal determination of what must be retained: ArchiveVault enforces
  the policy it's configured with; it doesn't decide the policy.