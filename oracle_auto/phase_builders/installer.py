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
            "Verify Oracle installer and patch ZIP files",
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
    integrity_checks = [
        line
        for file in files
        for line in (
            f"echo 'Integrity check: {file}'",
            f"unzip -t {sources}/{shlex.quote(file)} >/dev/null",
        )
    ]
    lines = [
        f"test -d {sources}",
        f"test -r {sources}",
        *checks,
        *integrity_checks,
        "echo 'Content check: gridSetup.sh'",
        f"unzip -l {sources}/{shlex.quote(config.installer.grid_zip)} | grep 'gridSetup.sh' >/dev/null",
        "echo 'Content check: runInstaller'",
        f"unzip -l {sources}/{shlex.quote(config.installer.db_zip)} | grep 'runInstaller' >/dev/null",
        "echo 'Readability check: grid installer ZIP'",
        f"sudo -iu grid test -r {sources}/{shlex.quote(config.installer.grid_zip)}",
        "echo 'Readability check: database installer ZIP'",
        f"sudo -iu oracle test -r {sources}/{shlex.quote(config.installer.db_zip)}",
        f"mkdir -p {STAGE}/installer-checks",
        f"ls -lh {sources} > {STAGE}/installer-checks/files.txt",
    ]
    return shell_script("Verify installer ZIP files", lines)
