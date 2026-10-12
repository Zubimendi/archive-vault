import os
import hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class EnvelopeCrypto:
    """
    Envelope Encryption for ArchiveVault CRYPTO_SHRED datasets.
    Key Hierarchy:
      Tenant KEK (Key Encryption Key)
        └── Subject DEK (Data Encryption Key) -> Encrypts subject's dataset rows
    """
    
    @staticmethod
    def derive_tenant_kek(kek_ref: str, master_secret_hex: str) -> bytes:
        """Derive or resolve tenant 256-bit KEK from tenant kek_ref and master secret."""
        h = hashlib.sha256()
        h.update(bytes.fromhex(master_secret_hex))
        h.update(kek_ref.encode('utf-8'))
        return h.digest()

    @staticmethod
    def generate_dek() -> bytes:
        """Generate a random 256-bit AES DEK for a data subject."""
        return AESGCM.generate_key(bit_length=256)

    @classmethod
    def wrap_dek(cls, tenant_kek: bytes, subject_dek: bytes) -> tuple[bytes, bytes]:
        """Wrap subject DEK using tenant KEK via AES-256-GCM."""
        aesgcm = AESGCM(tenant_kek)
        iv = os.urandom(12)
        wrapped_dek = aesgcm.encrypt(iv, subject_dek, associated_data=b"archivevault-dek-wrap")
        return wrapped_dek, iv

    @classmethod
    def unwrap_dek(cls, tenant_kek: bytes, wrapped_dek: bytes, iv: bytes) -> bytes:
        """Unwrap subject DEK using tenant KEK via AES-256-GCM."""
        aesgcm = AESGCM(tenant_kek)
        return aesgcm.decrypt(iv, wrapped_dek, associated_data=b"archivevault-dek-wrap")

    @classmethod
    def encrypt_data(cls, subject_dek: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
        """Encrypt dataset payload under subject DEK."""
        aesgcm = AESGCM(subject_dek)
        iv = os.urandom(12)
        ciphertext = aesgcm.encrypt(iv, plaintext, associated_data=None)
        return ciphertext, iv

    @classmethod
    def decrypt_data(cls, subject_dek: bytes, ciphertext: bytes, iv: bytes) -> bytes:
        """Decrypt dataset payload under subject DEK. Raises exception if DEK is invalid or shredded."""
        aesgcm = AESGCM(subject_dek)
        return aesgcm.decrypt(iv, ciphertext, associated_data=None)
