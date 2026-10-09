# ArchiveVault

A GDPR/CCPA data lifecycle and erasure service, built in Python.
Project 22 of a 15-week backend roadmap. The problem: "delete everything
about this user, everywhere, and be able to prove it," a legal
requirement most companies handle with a spreadsheet and a prayer.

Four ideas carry the project, each documented in `docs/ARCHITECTURE.md`:

1. **Retention policy as declarative config.** A versioned data map
   states where personal data lives and what happens to it, instead of
   `DELETE` statements scattered across services.
2. **Erasure as a saga with honest semantics.** Reversible quarantine
   first (erasure requests are an attack vector), then an explicit point
   of no return, then forward-only retriable steps. No pretend
   "compensation" for deletes that can't be undone.
3. **Crypto-shredding with the right key granularity.** Per-subject keys
   wrapped by per-tenant keys, so destroying one key makes every
   ciphertext copy, including backups, unreadable at once, with the
   caveats about scope and key-store backups stated plainly.
4. **Proof that doesn't retain the data.** Independent verification, a
   hash-chained ledger, and certificates that contain no personal data.

It also treats **coverage as a monitored property**: schema-drift
detection flags tables and columns nobody mapped, because unmapped data
is the most common real-world erasure failure.

Stack: Python, PostgreSQL, Docker. Reuses QueueLine (step execution),
Dispatcher/QueueLine (vendor webhooks), VaultKeep's envelope encryption
pattern, and DocSign's hash-chaining technique rather than rebuilding
them.

Documentation-first; see `docs/CURSOR_CONTEXT.md` for the build plan.
Engineering design only, not legal advice.

MIT licensed.