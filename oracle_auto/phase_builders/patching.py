"""Patching phase manual.

Owns OPatch replacement, configured patch application, and inventory capture.
Patch ordering follows the config exactly.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig, PatchConfig
from oracle_auto.phase_builders.common import DB_HOME, GRID_BASE, STAGE, make_step, safe_name


def apply_patch_steps(config: AutomationConfig) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for node in config.all_nodes:
        steps.append(
            make_step(
                "apply-patch",
                "update_opatch",
                node,
                "Update OPatch if OPatch ZIP is configured",
                _update_opatch_script(config),
                timeout=1200,
            )
        )
        for index, patch in enumerate(config.installer.patches, start=1):
            steps.append(
                make_step(
                    "apply-patch",
                    f"apply_patch_{index}_{safe_name(patch.label)}",
                    node,
                    f"Apply patch {patch.label}",
                    _apply_patch_script(config, patch),
                    timeout=7200,
                )
            )
        steps.append(
            make_step(
                "apply-patch",
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
        f"sudo -iu grid {GRID_BASE}/OPatch/opatch version",
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch version",
    ]
    return shell_script("Update OPatch", lines)


def _apply_patch_script(config: AutomationConfig, patch: PatchConfig) -> str:
    patch_zip = f"{config.installer.sources_path}/{patch.file}"
    patch_dir = f"{STAGE}/patches/{safe_name(patch.file)}"
    lines = [
        f"test -s {shlex.quote(patch_zip)}",
        f"mkdir -p {patch_dir}",
        f"unzip -oq {shlex.quote(patch_zip)} -d {patch_dir}",
        f"PATCH_TOP=$(find {patch_dir} -mindepth 1 -maxdepth 1 -type d | head -1)",
        'test -n "$PATCH_TOP"',
        f"{GRID_BASE}/OPatch/opatchauto apply \"$PATCH_TOP\" || sudo -iu oracle {DB_HOME}/OPatch/opatch apply -silent \"$PATCH_TOP\"",
    ]
    return shell_script(f"Apply patch {patch.label}", lines)


def _patch_inventory_script() -> str:
    lines = [
        f"sudo -iu grid {GRID_BASE}/OPatch/opatch lsinventory || true",
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch lsinventory",
    ]
    return shell_script("Collect patch inventory", lines)

