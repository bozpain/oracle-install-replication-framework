"""Installer verification phase manual.

Verifies that manually staged Oracle ZIP files exist on the target, are
readable, and are visible to the appropriate Oracle software owners.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig
from oracle_auto.phase_builders.common import STAGE, make_step


def verify_installer_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        make_step(
            "verify-installer",
            "verify_installer",
            node,
            "Verify Oracle installer, patch ZIP, and ASMLIB RPM files",
            _verify_installer_script(config),
            timeout=None,
        )
        for node in config.all_nodes
    ]


def _verify_installer_script(config: AutomationConfig) -> str:
    sources = shlex.quote(config.installer.sources_path)
    files = [config.installer.grid_zip, config.installer.db_zip]
    if config.installer.opatch_zip:
        files.append(config.installer.opatch_zip)
    files.extend(patch.file for patch in config.installer.patches)
    checks = [f"test -s {sources}/{shlex.quote(file)}" for file in files]
    asmlib_case_lines = []
    if config.asm.storage_mode == "asmlibv3":
        asmlib_case_lines = [
            "echo 'ASMLIB RPM check'",
            "arch=$(uname -m)",
            "case \"$arch\" in",
            *[
                f"  {shlex.quote(arch)}) test -s {sources}/{shlex.quote(rpm)} ;;"
                for arch, rpm in sorted(config.os.asmlib_rpms.items())
            ],
            "  *) echo \"Unsupported ASMLIB architecture: $arch\" >&2; exit 1 ;;",
            "esac",
        ]
    verify_cache = f"{STAGE}/installer-checks/zip-integrity"
    integrity_helpers = [
        f"VERIFY_CACHE_DIR={verify_cache}",
        "mkdir -p \"$VERIFY_CACHE_DIR\"",
        "verify_zip_integrity() {",
        "  file=\"$1\"",
        f"  path={sources}/\"$file\"",
        "  marker=\"$VERIFY_CACHE_DIR/$(printf '%s' \"$file\" | sed 's/[^A-Za-z0-9_.-]/_/g').ok\"",
        "  signature=$(stat -c '%s:%Y' \"$path\")",
        "  if test -r \"$marker\" && grep -qx \"$signature\" \"$marker\"; then",
        "    echo \"Integrity check: $file (cached)\"",
        "    return 0",
        "  fi",
        "  if test \"${ORACLE_AUTO_FULL_ZIP_VERIFY:-false}\" != true; then",
        "    echo \"Integrity check: $file (skipped; set ORACLE_AUTO_FULL_ZIP_VERIFY=true for unzip -t)\"",
        "    return 0",
        "  fi",
        "  echo \"Integrity check: $file\"",
        "  unzip -t \"$path\" >/dev/null",
        "  printf '%s\\n' \"$signature\" > \"$marker\"",
        "}",
    ]
    integrity_checks = [f"verify_zip_integrity {shlex.quote(file)}" for file in files]
    lines = [
        f"test -d {sources}",
        f"test -r {sources}",
        *checks,
        *integrity_helpers,
        *integrity_checks,
        "echo 'Content check: gridSetup.sh'",
        f"unzip -l {sources}/{shlex.quote(config.installer.grid_zip)} | grep 'gridSetup.sh' >/dev/null",
        "echo 'Content check: runInstaller'",
        f"unzip -l {sources}/{shlex.quote(config.installer.db_zip)} | grep 'runInstaller' >/dev/null",
        "echo 'Readability check: grid installer ZIP'",
        f"sudo -iu grid test -r {sources}/{shlex.quote(config.installer.grid_zip)}",
        "echo 'Readability check: database installer ZIP'",
        f"sudo -iu oracle test -r {sources}/{shlex.quote(config.installer.db_zip)}",
        *asmlib_case_lines,
        f"mkdir -p {STAGE}/installer-checks",
        f"ls -lh {sources} > {STAGE}/installer-checks/files.txt",
    ]
    return shell_script("Verify installer ZIP and RPM files", lines)
