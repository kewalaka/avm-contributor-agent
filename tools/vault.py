"""CredentialVault — runtime secret registry with output redaction.

Provides a thin CredentialVault that resolves logical-name → token mappings
from environment variables and redacts known secret values from any string
returned by tool wrappers before it reaches the model's context window.

Interface-compatible with a future Azure Key Vault backend — swap
``_load_from_env`` with a Key Vault client call without changing callers.

Usage::

    from tools.vault import vault          # module-level singleton
    result = vault.redact(tool_output)     # replaces known secrets with ***REDACTED***
    token  = vault.resolve("AGENT_DISPATCH_TOKEN")
"""

from __future__ import annotations

import os

# Env var names whose values must never appear in tool output
_SECRET_ENV_VARS: list[str] = [
    "AGENT_DISPATCH_TOKEN",
    "AZURE_CLIENT_SECRET",
    "AZURE_CLIENT_ID",
    "AZURE_AI_PROJECT_ENDPOINT",
    "GITHUB_TOKEN",
    "GH_TOKEN",
]

_REDACTED = "***REDACTED***"


class CredentialVault:
    """Logical name → token registry with output redaction.

    All registered secret *values* (not names) are replaced with
    ``***REDACTED***`` when :meth:`redact` is called. Only values
    longer than 4 characters are tracked to avoid false-positive
    redaction of short default values or placeholders.
    """

    def __init__(self, extra_secrets: dict[str, str] | None = None) -> None:
        self._secrets: dict[str, str] = {}
        for name in _SECRET_ENV_VARS:
            value = os.environ.get(name, "")
            if value and len(value) > 4:
                self._secrets[name] = value
        if extra_secrets:
            for name, value in extra_secrets.items():
                if value and len(value) > 4:
                    self._secrets[name] = value

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, name: str) -> str:
        """Return the secret value for a logical name, or empty string."""
        return self._secrets.get(name, "")

    def redact(self, output: str) -> str:
        """Replace all known secret values in *output* with ``***REDACTED***``.

        Non-string inputs are returned unchanged so callers do not need
        to guard the type before calling.
        """
        if not isinstance(output, str):
            return output
        for value in self._secrets.values():
            if value in output:
                output = output.replace(value, _REDACTED)
        return output

    def register(self, name: str, value: str) -> None:
        """Register an additional secret at runtime (e.g. a dynamically
        generated token returned by an auth call).
        """
        if value and len(value) > 4:
            self._secrets[name] = value


# Module-level singleton — import and use directly:
#   from tools.vault import vault
vault = CredentialVault()
