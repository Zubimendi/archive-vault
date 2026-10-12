# Title: Deleting Someone Is Harder Than Storing Them — and Proving It Is Harder Still

## Short version (LinkedIn)
Project 22: ArchiveVault, a GDPR/CCPA erasure service. Four lessons
that don't show up in tutorials:

1. Erasure isn't a saga with compensation. You can't un-delete, and the
   legal duty is to finish. So I split it into a reversible quarantine
   (because forged deletion requests are a real attack), an explicit
   point of no return, then forward-only retries.
2. "Anonymized" with a SHA-256 of the email is pseudonymized. Anyone with
   a list of emails can re-link it. It's still personal data.
3. Crypto-shredding is the only clean answer to backups, but the key
   store's own backups can quietly undo it.
4. The proof of deletion can't contain the thing it proves you deleted.
   An audit log saying "erased jane@example.com" is a new copy of
   Jane's email.

Plus the failure that actually bites in production: data nobody mapped.
So coverage became an alert, not an audit. Repo: <your-fork-url>

## Conclusion
Compliance engineering gets dismissed as paperwork, but nearly every
interesting problem in it is a distributed-systems or security problem
wearing a legal hat: sagas without a clean rollback, irreversibility
you have to actually verify, tamper-evident evidence that mustn't leak
what it records, and a policy surface that rots every time engineering
ships. The real skill isn't knowing the regulation's article numbers;
it's noticing where the obvious engineering answer (compensate the
saga, hash the email, log what you deleted) quietly violates the
requirement it was meant to satisfy. Few engineers can talk about that
from having built it, which is exactly why it's worth showing.

## Medium Tags & Publishing Metadata
- **Tags (5/5 for Medium publishing):**
  - `Data Privacy`
  - `GDPR`
  - `System Design`
  - `Software Architecture`
  - `Cybersecurity`
- **Secondary / Topic Tags:** `Backend`, `Python`, `Cryptography`, `Data Engineering`
- **Subtitle:** Why GDPR Article 17 is a distributed systems problem wearing a legal hat — sagas without rollback, pseudo vs. true anonymization, and the proof-of-erasure paradox.
- **Estimated Reading Time:** 6 min read
- **Thumbnail / Hero Image:** `docs/assets/archive_vault_thumbnail.jpg`
- **Target Publications:** *Better Programming*, *Towards Data Science*, *ITNEXT*, or *Level Up Coding*

## Interview talking points
1. Lead with "erasure can't be compensated," then explain the
   quarantine → pivot → retriable structure and why quarantine exists
   (forged-request attack).
2. Pseudonymization vs anonymization, using the hashed-email example.
3. Why crypto-shredding needs per-subject keys, and the key-store-backup
   circularity caveat. Volunteering the caveat is the credibility move.
4. The proof-of-erasure paradox and how the ledger avoids retaining PII.
5. Coverage monitoring: reframing "know where the data is" as alerting.
6. Be explicit about limits: ML models and unstructured data are not
   solved, and saying so is a strength.