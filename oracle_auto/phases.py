"""Phase facade manual.

This module intentionally stays small. The CLI imports phase builders from here
for a stable public surface, while implementation lives in
`oracle_auto.phase_builders.*` modules grouped by automation domain.
"""

from __future__ import annotations

from oracle_auto.phase_builders.database import create_database_steps, install_db_software_steps
from oracle_auto.phase_builders.dataguard import setup_active_dataguard_steps, setup_dataguard_broker_steps
from oracle_auto.phase_builders.diagnostics import cleanup_lab_steps, collect_diagnostics_steps
from oracle_auto.phase_builders.grid import install_grid_steps
from oracle_auto.phase_builders.installer import verify_installer_steps
from oracle_auto.phase_builders.os import prepare_os_steps
from oracle_auto.phase_builders.patching import apply_patch_steps
from oracle_auto.phase_builders.role import failover_steps, switchover_steps
from oracle_auto.phase_builders.storage import (
    configure_asm_storage_steps,
    prepare_storage_rules_steps,
    prepare_storage_steps,
)
from oracle_auto.phase_builders.validation import validate_deployment_steps

__all__ = [
    "apply_patch_steps",
    "cleanup_lab_steps",
    "collect_diagnostics_steps",
    "configure_asm_storage_steps",
    "create_database_steps",
    "failover_steps",
    "install_db_software_steps",
    "install_grid_steps",
    "prepare_os_steps",
    "prepare_storage_rules_steps",
    "prepare_storage_steps",
    "setup_active_dataguard_steps",
    "setup_dataguard_broker_steps",
    "switchover_steps",
    "validate_deployment_steps",
    "verify_installer_steps",
]
