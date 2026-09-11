"""Operator-owned sandbox settings. No request can choose a runtime or image."""
from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxSettings:
    enabled: bool = False
    vault_key: str | None = None
    runtime: str = "runsc"
    image: str = "breakroom-sandbox:local"
    development_only: bool = False
    openai_models: tuple[str, ...] = ("gpt-4.1-mini",)
    anthropic_models: tuple[str, ...] = ("claude-sonnet-4-6",)

    @classmethod
    def from_environment(cls):
        return cls(enabled=os.getenv("BREAKROOM_SANDBOX_ENABLED") == "1",
            vault_key=os.getenv("BREAKROOM_SANDBOX_VAULT_KEY") or None,
            runtime=os.getenv("BREAKROOM_SANDBOX_RUNTIME", "runsc"),
            image=os.getenv("BREAKROOM_SANDBOX_IMAGE", "breakroom-sandbox:local"),
            development_only=os.getenv("BREAKROOM_SANDBOX_TRUSTED_DEV") == "1",
            openai_models=tuple(filter(None, os.getenv("BREAKROOM_SANDBOX_OPENAI_MODELS", "gpt-4.1-mini").split(","))),
            anthropic_models=tuple(filter(None, os.getenv("BREAKROOM_SANDBOX_ANTHROPIC_MODELS", "claude-sonnet-4-6").split(","))))

    def validate(self, environment: str):
        if self.runtime not in {"runsc", "runc"}:
            raise ValueError("Sandbox runtime must be runsc or explicit trusted development runc")
        if self.runtime != "runsc" and not (self.development_only and environment in {"development", "test"}):
            raise ValueError("Untrusted hosted execution requires gVisor/runsc")
        if self.development_only and environment == "production":
            raise ValueError("Trusted development execution is prohibited in production")
        if not self.image or len(self.image) > 256 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:-]*", self.image):
            raise ValueError("Invalid operator sandbox image")
        for models in (self.openai_models, self.anthropic_models):
            if len(models) > 20 or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", model) for model in models):
                raise ValueError("Model allowlists contain invalid model identifiers")
        if self.enabled:
            try:
                key = base64.b64decode(self.vault_key or "", altchars=b"-_", validate=True)
            except (ValueError, TypeError):
                key = b""
            if len(key) != 32:
                raise ValueError("Enabled sandboxes require a base64-encoded 32-byte vault key")

    def models(self):
        return {"openai": list(self.openai_models), "anthropic": list(self.anthropic_models)}
