"""Authenticated encryption binds each stored secret/source to its tenant row."""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class Vault:
    def __init__(self, key: str):
        raw = base64.b64decode(key, altchars=b"-_", validate=True)
        if len(raw) != 32:
            raise ValueError("Vault key must contain 32 bytes")
        self._cipher = AESGCM(raw)

    @staticmethod
    def _scope(project_id: str, kind: str, row_id: str):
        return ("breakroom-sandbox-v1:" + project_id + ":" + kind + ":" + row_id).encode("ascii")

    def encrypt(self, value: bytes, project_id: str, kind: str, row_id: str) -> str:
        nonce = os.urandom(12)
        sealed = self._cipher.encrypt(nonce, value, self._scope(project_id, kind, row_id))
        return base64.b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, value: str, project_id: str, kind: str, row_id: str) -> bytes:
        raw = base64.b64decode(value, validate=True)
        return self._cipher.decrypt(raw[:12], raw[12:], self._scope(project_id, kind, row_id))
