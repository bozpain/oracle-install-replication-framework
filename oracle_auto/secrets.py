"""Secret redaction helper manual.

Automation commands should reference target-side environment variable names,
not literal passwords. This module is an additional control for local logs and
reports: when the same secret values also exist on the control machine, they are
redacted before state, HTML, JSON, or per-step log files are written.
"""

from __future__ import annotations

import os
from collections.abc import Iterable


DEFAULT_SECRET_ENV_NAMES = {
    "ORACLE_AUTO_SYS_PASSWORD",
    "ORACLE_AUTO_SYSTEM_PASSWORD",
    "ORACLE_AUTO_ASMSNMP_PASSWORD",
    "ORACLE_AUTO_DG_PASSWORD",
}


def redact(text: str, extra_env_names: Iterable[str] | None = None) -> str:
    redacted = text
    for value in _secret_values(extra_env_names):
        redacted = redacted.replace(value, "***REDACTED***")
    return redacted


def _secret_values(extra_env_names: Iterable[str] | None) -> list[str]:
    names = set(DEFAULT_SECRET_ENV_NAMES)
    if extra_env_names:
        names.update(name for name in extra_env_names if name)
    names.update(name for name in os.environ if name.startswith("ORACLE_AUTO_") and "PASSWORD" in name)

    values: list[str] = []
    for name in sorted(names):
        value = os.environ.get(name)
        if value and len(value) >= 4:
            values.append(value)
    return values
