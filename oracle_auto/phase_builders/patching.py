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
    INVENTORY_LOCATION,
    make_step,
    oracle_home_inventory_pointer_lines,
    oracle_user_group_lines,
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
                "Collect Oracle home patch inventory summary",
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
        pointer_lines = _db_home_opatch_repair_lines()
    lines = [
        *pointer_lines,
        *stage_patch_lines(config.installer.sources_path, patch.file, patch_id=patch.patch_id),
        prereq,
    ]
    return shell_script(f"Analyze {target} patch {patch.label}", lines)


def _apply_grid_patch_script(config: AutomationConfig, patch: PatchConfig) -> str:
    patch_dir = _patch_dir(config, patch)
    lines = [
        *oracle_home_inventory_pointer_lines(GRID_BASE, "grid"),
        *stage_patch_lines(config.installer.sources_path, patch.file, patch_id=patch.patch_id),
        patch_top_assignment(patch_dir, prefer_self=patch.patch_id is not None),
        "GRID_PATCH_APPLY_LOG=$(mktemp /tmp/oracle-auto-grid-patch.XXXXXX)",
        "set +e",
        f"{GRID_BASE}/OPatch/opatchauto apply \"$PATCH_TOP\" -oh {GRID_BASE} 2>&1 | tee \"$GRID_PATCH_APPLY_LOG\"",
        "patch_rc=${PIPESTATUS[0]}",
        "set -e",
        "if test \"$patch_rc\" -ne 0; then",
        "  if grep -qiE 'already.*(applied|installed)|no patches need|not needed' \"$GRID_PATCH_APPLY_LOG\"; then",
        "    echo 'Grid patch already applied; continuing.'",
        "  else",
        "    exit \"$patch_rc\"",
        "  fi",
        "fi",
    ]
    return shell_script(f"Apply Grid patch {patch.label}", lines)


def _apply_db_patch_script(config: AutomationConfig, patch: PatchConfig) -> str:
    patch_dir = _patch_dir(config, patch)
    lines = [
        *_db_home_opatch_repair_lines(),
        *stage_patch_lines(config.installer.sources_path, patch.file, patch_id=patch.patch_id),
        patch_top_assignment(patch_dir, prefer_self=patch.patch_id is not None),
        "DB_PATCH_APPLY_LOG=$(mktemp /tmp/oracle-auto-db-patch.XXXXXX)",
        "set +e",
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch apply -silent \"$PATCH_TOP\" 2>&1 | tee \"$DB_PATCH_APPLY_LOG\"",
        "patch_rc=${PIPESTATUS[0]}",
        "set -e",
        "if test \"$patch_rc\" -ne 0; then",
        "  if grep -qiE 'already.*(applied|installed)|no patches need|not needed' \"$DB_PATCH_APPLY_LOG\"; then",
        "    echo 'Database patch already applied; continuing.'",
        "  else",
        "    exit \"$patch_rc\"",
        "  fi",
        "fi",
    ]
    return shell_script(f"Apply Database patch {patch.label}", lines)


def _apply_ojvm_patch_script(config: AutomationConfig, patch: PatchConfig) -> str:
    if patch.patch_id:
        inventory_validation = [
            f"OJVM_PATCH_ID={shlex.quote(patch.patch_id)}",
            "echo \"Validating OJVM patch $OJVM_PATCH_ID in DB home patch list.\"",
            "OJVM_PATCH_INVENTORY_LOG=$(mktemp /tmp/oracle-auto-ojvm-inventory.XXXXXX)",
            f"sudo -iu oracle {DB_HOME}/OPatch/opatch lspatches 2>&1 | tee \"$OJVM_PATCH_INVENTORY_LOG\"",
            "if ! grep -Eq \"^$OJVM_PATCH_ID([;[:space:]]|$)\" \"$OJVM_PATCH_INVENTORY_LOG\"; then",
            "  echo \"ERROR: OJVM patch $OJVM_PATCH_ID is not visible in DB home patch list after apply.\" >&2",
            "  cat \"$OJVM_PATCH_INVENTORY_LOG\" >&2",
            "  exit 1",
            "fi",
        ]
    else:
        inventory_validation = [
            "echo 'No OJVM patch_id configured; collecting DB home inventory after OJVM apply.'",
            f"sudo -iu oracle {DB_HOME}/OPatch/opatch lsinventory",
        ]
    lines = [
        *_db_home_opatch_repair_lines(),
        *stage_patch_lines(config.installer.sources_path, patch.file, patch_id=patch.patch_id),
        "OJVM_PATCH_APPLY_LOG=$(mktemp /tmp/oracle-auto-ojvm-patch.XXXXXX)",
        "set +e",
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch apply -silent \"$PATCH_TOP\" 2>&1 | tee \"$OJVM_PATCH_APPLY_LOG\"",
        "patch_rc=${PIPESTATUS[0]}",
        "set -e",
        "if test \"$patch_rc\" -ne 0; then",
        "  if grep -qiE 'already.*(applied|installed)|no patches need|not needed' \"$OJVM_PATCH_APPLY_LOG\"; then",
        "    echo 'OJVM patch already applied; continuing.'",
        "  else",
        "    exit \"$patch_rc\"",
        "  fi",
        "fi",
        *inventory_validation,
    ]
    return shell_script(f"Apply OJVM patch {patch.label}", lines)


def _datapatch_script() -> str:
    lines = [
        *_db_home_opatch_repair_lines(),
        f"sudo -iu oracle {DB_HOME}/OPatch/datapatch -verbose",
    ]
    return shell_script("Run datapatch", lines)


def _patch_inventory_script() -> str:
    lines = [
        *oracle_home_inventory_pointer_lines(GRID_BASE, "grid"),
        *oracle_home_inventory_pointer_lines(DB_HOME, "oracle"),
        "echo '== Grid home version =='",
        f"sudo -iu grid {GRID_BASE}/bin/oraversion -compositeVersion || sudo -iu grid {GRID_BASE}/bin/oraversion -version || true",
        "echo '== Grid OPatch version =='",
        f"sudo -iu grid {GRID_BASE}/OPatch/opatch version || true",
        "echo '== Grid patches =='",
        f"sudo -iu grid {GRID_BASE}/OPatch/opatch lspatches || true",
        "echo '== Database home version =='",
        f"sudo -iu oracle {DB_HOME}/bin/oraversion -compositeVersion || sudo -iu oracle {DB_HOME}/bin/oraversion -version",
        "echo '== Database OPatch version =='",
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch version",
        "echo '== Database patches =='",
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch lspatches",
    ]
    return shell_script("Collect Oracle home patch inventory summary", lines)


def _db_home_opatch_repair_lines() -> list[str]:
    return [
        *oracle_user_group_lines(),
        *oracle_home_inventory_pointer_lines(DB_HOME, "oracle"),
        f"mkdir -p {DB_HOME}/.patch_storage {DB_HOME}/cfgtoollogs {INVENTORY_LOCATION}/logs",
        f"chown -R oracle:oinstall {DB_HOME}/.patch_storage {DB_HOME}/cfgtoollogs {DB_HOME}/OPatch 2>/dev/null || true",
        f"chmod -R u+rwX,g+rwX {DB_HOME}/.patch_storage {DB_HOME}/cfgtoollogs {DB_HOME}/OPatch 2>/dev/null || true",
        f"chgrp -R oinstall {INVENTORY_LOCATION} 2>/dev/null || true",
        f"chmod -R g+rwX {INVENTORY_LOCATION} 2>/dev/null || true",
        "if ! (pgrep -x runInstaller || pgrep -x opatch || pgrep -x opatchauto || pgrep -x oui) >/dev/null 2>&1; then",
        f"  find {DB_HOME}/.patch_storage {INVENTORY_LOCATION}/locks -type f \\( -name '*.lock' -o -name 'lock' \\) -delete 2>/dev/null || true",
        "fi",
        f"if test -r {INVENTORY_LOCATION}/ContentsXML/inventory.xml && ! grep -Fq 'LOC=\"{DB_HOME}\"' {INVENTORY_LOCATION}/ContentsXML/inventory.xml; then",
        "  echo 'Database home missing from central inventory; attaching home before OPatch.'",
        f"  test -x {DB_HOME}/runInstaller",
        "  DB_ATTACH_LOG=/u01/stage/logs/dbhome_attach_$(date +%Y%m%d%H%M%S).log",
        "  DB_ATTACH_HOME_NAME=OraDB19Home$(hostname -s | tr -cd '[:alnum:]')$(date +%Y%m%d%H%M%S)",
        "  set +e",
        f"  sudo -iu oracle env ORACLE_HOME={DB_HOME} ORACLE_BASE=/u01/app/oracle {DB_HOME}/runInstaller -silent -waitforcompletion -attachHome -invPtrLoc {DB_HOME}/oraInst.loc ORACLE_HOME={DB_HOME} ORACLE_HOME_NAME=\"$DB_ATTACH_HOME_NAME\" ORACLE_BASE=/u01/app/oracle -ignorePrereq 2>&1 | tee \"$DB_ATTACH_LOG\"",
        "  attach_rc=${PIPESTATUS[0]}",
        "  set -e",
        "  if test \"$attach_rc\" -ne 0; then",
        "    echo \"ERROR: Database home attach failed. See $DB_ATTACH_LOG\"",
        "    grep -iE 'FATAL|ERROR|INS-|PRVF-|SEVERE|failed|failure' \"$DB_ATTACH_LOG\" || true",
        "    exit \"$attach_rc\"",
        "  fi",
        "fi",
        f"test -r {INVENTORY_LOCATION}/ContentsXML/inventory.xml",
        f"if ! grep -Fq 'LOC=\"{DB_HOME}\"' {INVENTORY_LOCATION}/ContentsXML/inventory.xml; then",
        f"  echo 'ERROR: Database home is still missing from central inventory after attachHome.'",
        f"  grep -nE '<HOME .*LOC=' {INVENTORY_LOCATION}/ContentsXML/inventory.xml || true",
        "  exit 1",
        "fi",
        f"sudo -iu oracle test -w {DB_HOME}/.patch_storage",
    ]


def _configured_patches(config: AutomationConfig) -> list[tuple[str, PatchConfig]]:
    patches: list[tuple[str, PatchConfig]] = []
    if config.installer.grid_patch is not None:
        patches.append(("grid", config.installer.grid_patch))
    if config.installer.db_patch is not None:
        patches.append(("db", config.installer.db_patch))
    if config.installer.ojvm_patch is not None:
        patches.append(("ojvm", config.installer.ojvm_patch))
    return patches


def _patch_dir(config: AutomationConfig, patch: PatchConfig) -> str:
    if patch.patch_id:
        return f"{config.installer.sources_path}/{patch.patch_id}"
    return config.installer.sources_path
