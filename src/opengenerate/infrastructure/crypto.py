"""Field-level encryption at rest with a rotatable key ring (Fernet).

Ciphertext format: ``v1:<key-id>:<fernet-token>``; the key id is the first 8 hex chars of
SHA-256(key) so any key in the ring can decrypt and rotation is a re-encrypt.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from cryptography.fernet import Fernet, InvalidToken

_PREFIX = "v1"


class DecryptionError(Exception):
    """Ciphertext is malformed, tampered with, or its key is not in the ring."""


def key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:8]


class FieldCipher:
    def __init__(self, keys: Sequence[str]) -> None:
        if not keys:
            raise ValueError("at least one key is required")
        self._ring = {key_id(k): Fernet(k.encode()) for k in keys}
        self._primary_id = key_id(keys[0])

    def __repr__(self) -> str:  # never reveal key material
        return f"FieldCipher(keys={len(self._ring)}, primary={self._primary_id})"

    def encrypt(self, plaintext: str) -> str:
        token = self._ring[self._primary_id].encrypt(plaintext.encode()).decode()
        return f"{_PREFIX}:{self._primary_id}:{token}"

    def decrypt(self, ciphertext: str) -> str:
        parts = ciphertext.split(":", 2)
        if len(parts) != 3 or parts[0] != _PREFIX:
            raise DecryptionError("malformed ciphertext")
        fernet = self._ring.get(parts[1])
        if fernet is None:
            raise DecryptionError("encryption key is not available")
        try:
            return fernet.decrypt(parts[2].encode()).decode()
        except InvalidToken:
            raise DecryptionError("ciphertext failed authentication") from None

    def needs_rotation(self, ciphertext: str) -> bool:
        return not ciphertext.startswith(f"{_PREFIX}:{self._primary_id}:")

    def rotate(self, ciphertext: str) -> str:
        return self.encrypt(self.decrypt(ciphertext))
