import hmac
import hashlib

def normalize_identifier(identifier: str) -> str:
    """Normalize identifier prior to HMAC hashing (lowercased, stripped)."""
    return identifier.strip().lower()

def compute_suppression_hmac(pepper: str, identifier: str) -> str:
    """
    Compute HMAC-SHA256 for the suppression ledger using an isolated secret pepper.
    This creates a pseudonymous digest retained specifically for restore-replay protection.
    """
    normalized = normalize_identifier(identifier).encode('utf-8')
    key = pepper.encode('utf-8')
    return hmac.new(key, normalized, hashlib.sha256).hexdigest()
