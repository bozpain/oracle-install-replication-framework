"""Patching phase manual.

Owns OPatch replacement, configured patch application, and inventory capture.
Patch ordering follows the config exactly.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig, PatchConfig
from oracle_auto.phase_builders.common import (
    DB_HOME,
    GRID_BASE,
    make_step,
    oracle_home_inventory_pointer_lines,
    patch_top_assignment,
    safe_name,
    stage_patch_lines,
)


def apply_patch_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        *update_opatch_steps(config),
        *analyze_patch_steps(config),
        *apply_grid_patch_steps(config),
        *apply_db_patch_steps(config),
        *apply_ojvm_patch_steps(config),
        *datapatch_steps(config),
        *patch_inventory_steps(config),
    ]


def update_opatch_steps(config: AutomationConfig) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for node in config.all_nodes:
        steps.append(
            make_step(
                "update-opatch",
                "update_opatch",
                node,
                "Update OPatch if OPatch ZIP is configured",
                _update_opatch_script(config),
                timeout=1200,
            )
        )
    return steps


def analyze_patch_steps(config: AutomationConfig) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for node in config.all_nodes:
        for target, patch in _configured_patches(config):
            steps.append(
                make_step(
                    "analyze-patch",
                    f"analyze_{target}_patch_{safe_name(patch.label)}",
                    node,
                    f"Analyze {target} patch {patch.label}",
                    _analyze_patch_script(config, target, patch),
                    timeout=1800,
                )
            )
    return steps


def apply_grid_patch_steps(config: AutomationConfig) -> list[AutomationStep]:
    patch = config.installer.grid_patch
    if patch is None:
        return []
    steps: list[AutomationStep] = []
    for node in config.all_nodes:
        steps.append(
            make_step(
                "apply-grid-patch",
                f"apply_grid_patch_{safe_name(patch.label)}",
                node,
                f"Apply Grid patch {patch.label}",
                _apply_grid_patch_script(config, patch),
                timeout=7200,
            )
        )
    return steps


def apply_db_patch_steps(config: AutomationConfig) -> list[AutomationStep]:
    patch = config.installer.db_patch
    if patch is None:
        return []
    steps: list[AutomationStep] = []
    for node in config.all_nodes:
        steps.append(
            make_step(
                "apply-db-patch",
                f"apply_db_patch_{safe_name(patch.label)}",
                node,
                f"Apply Database patch {patch.label}",
                _apply_db_patch_script(config, patch),
                timeout=7200,
            )
        )
    return steps


def apply_ojvm_patch_steps(config: AutomationConfig) -> list[AutomationStep]:
    patch = config.installer.ojvm_patch
    if patch is None:
        return []
    steps: list[AutomationStep] = []
    for node in config.all_nodes:
        steps.append(
            make_step(
                "apply-ojvm-patch",
                f"apply_ojvm_patch_{safe_name(patch.label)}",
                node,
                f"Apply OJVM patch {patch.label}",
                _apply_ojvm_patch_script(config, patch),
                timeout=7200,
            )
        )
    return steps


def datapatch_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        make_step(
            "datapatch",
            "run_datapatch",
            config.primary_site.nodes[0],
            "Run datapatch on primary database home",
            _datapatch_script(),
            timeout=3600,
        )
    ]


def patch_inventory_steps(config: AutomationConfig) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for node in config.all_nodes:
        steps.append(
            make_step(
                "patch-inventory",
                "patch_inventory",
                node,
                "Collect OPatch inventory",
                _patch_inventory_script(),
                timeout=600,
            )
        )
    return steps


def _update_opatch_script(config: AutomationConfig) -> str:
    if not config.installer.opatch_zip:
        return shell_script("Skip OPatch update", ["echo 'No OPatch ZIP configured; skipping.'"])
    opatch_zip = f"{config.installer.sources_path}/{config.installer.opatch_zip}"
    lines = [
        f"test -s {shlex.quote(opatch_zip)}",
        f"mv {GRID_BASE}/OPatch {GRID_BASE}/OPatch.bak.$(date +%Y%m%d%H%M%S) 2>/dev/null || true",
        f"mv {DB_HOME}/OPatch {DB_HOME}/OPatch.bak.$(date +%Y%m%d%H%M%S) 2>/dev/null || true",
        f"sudo -iu grid unzip -oq {shlex.quote(opatch_zip)} -d {GRID_BASE}",
        f"sudo -iu oracle unzip -oq {shlex.quote(opatch_zip)} -d {DB_HOME}",
        *oracle_home_inventory_pointer_lines(GRID_BASE, "grid"),
        *oracle_home_inventory_pointer_lines(DB_HOME, "oracle"),
        f"sudo -iu grid {GRID_BASE}/OPatch/opatch version",
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch version",
    ]
    return shell_script("Update OPatch", lines)


def _apply_patch_script(config: AutomationConfig, patch: PatchConfig) -> str:
    lines = [
        *oracle_home_inventory_pointer_lines(GRID_BASE, "grid"),
        *oracle_home_inventory_pointer_lines(DB_HOME, "oracle"),
        *stage_patch_lines(config.installer.sources_path, patch.file, patch_id=patch.patch_id),
        f"{GRID_BASE}/OPatch/opatchauto apply \"$PATCH_TOP\" || sudo -iu oracle {DB_HOME}/OPatch/opatch apply -silent \"$PATCH_TOP\"",
    ]
    return shell_script(f"Apply patch {patch.label}", lines)


def _analyze_patch_script(config: AutomationConfig, target: str, patch: PatchConfig) -> str:
    if target == "grid":
        prereq = f"{GRID_BASE}/OPatch/opatchauto apply \"$PATCH_TOP\" -analyze"
        pointer_lines = oracle_home_inventory_pointer_lines(GRID_BASE, "grid")
    else:
        prereq = f"sudo -iu oracle {DB_HOME}/OPatch/opatch prereq CheckConflictAgainstOHWithDetail -phBaseDir \"$PATCH_TOP\""
        pointer_lines = oracle_home_inventory_pointer_lines(DB_HOME, "oracle")
    lines = [
        *pointer_lines,
        *stage_patch_lines(config.installer.sources_path, patch.file, patch_id=patch.patch_id),
        prereq,
    ]
    return shell_script(f"Analyze {target} patch {patch.label}", lines)


def _apply_grid_patch_script(config: AutomationConfig, patch: PatchConfig) -> str:
    patch_dir = _patch_dir(patch)
    lines = [
        *oracle_home_inventory_pointer_lines(GRID_BASE, "grid"),
        patch_top_assignment(patch_dir, patch_id=patch.patch_id),
        f"{GRID_BASE}/OPatch/opatchauto apply \"$PATCH_TOP\" -oh {GRID_BASE}",
    ]
    return shell_script(f"Apply Grid patch {patch.label}", lines)


def _apply_db_patch_script(config: AutomationConfig, patch: PatchConfig) -> str:
    patch_dir = _patch_dir(patch)
    lines = [
        *oracle_home_inventory_pointer_lines(DB_HOME, "oracle"),
        patch_top_assignment(patch_dir, patch_id=patch.patch_id),
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch apply -silent \"$PATCH_TOP\"",
    ]
    return shell_script(f"Apply Database patch {patch.label}", lines)


def _apply_ojvm_patch_script(config: AutomationConfig, patch: PatchConfig) -> str:
    lines = [
        *oracle_home_inventory_pointer_lines(DB_HOME, "oracle"),
        *stage_patch_lines(config.installer.sources_path, patch.file, patch_id=patch.patch_id),
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch apply -silent \"$PATCH_TOP\"",
    ]
    return shell_script(f"Apply OJVM patch {patch.label}", lines)


def _datapatch_script() -> str:
    lines = [
        *oracle_home_inventory_pointer_lines(DB_HOME, "oracle"),
        f"sudo -iu oracle {DB_HOME}/OPatch/datapatch -verbose",
    ]
    return shell_script("Run datapatch", lines)


def _patch_inventory_script() -> str:
    lines = [
        *oracle_home_inventory_pointer_lines(GRID_BASE, "grid"),
        *oracle_home_inventory_pointer_lines(DB_HOME, "oracle"),
        f"sudo -iu grid {GRID_BASE}/OPatch/opatch lsinventory || true",
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch lsinventory",
    ]
    return shell_script("Collect patch inventory", lines)


def _configured_patches(config: AutomationConfig) -> list[tuple[str, PatchConfig]]:
    patches: list[tuple[str, PatchConfig]] = []
    if config.installer.grid_patch is not None:
        patches.append(("grid", config.installer.grid_patch))
    if config.installer.db_patch is not None:
        patches.append(("db", config.installer.db_patch))
    if config.installer.ojvm_patch is not None:
        patches.append(("ojvm", config.installer.ojvm_patch))
    return patches


def _patch_dir(patch: PatchConfig) -> str:
    return f"/u01/stage/patches/{safe_name(patch.file)}"
