"""Shared phase builder manual.

Keep cross-phase constants and tiny helpers here. Phase modules should import
these helpers instead of redefining Oracle home paths or step construction.
"""

from __future__ import annotations

from oracle_auto.automation import AutomationStep
from oracle_auto.config import NodeConfig


GRID_BASE = "/u01/app/19.0.0/grid"
GRID_BASE_DIR = "/u01/app/grid"
ORACLE_BASE = "/u01/app/oracle"
DB_HOME = "/u01/app/oracle/product/19.0.0/dbhome_1"
STAGE = "/u01/stage"


def make_step(
    phase: str,
    name: str,
    node: NodeConfig,
    title: str,
    command: str,
    timeout: int,
    warn_only: bool = False,
) -> AutomationStep:
    return AutomationStep(
        phase=phase,
        name=name,
        node=node,
        title=title,
        command=command,
        timeout=timeout,
        warn_only=warn_only,
    )


def safe_name(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_").lower()

