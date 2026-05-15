"""Shared phase builder manual.

Keep cross-phase constants and tiny helpers here. Phase modules should import
these helpers instead of redefining Oracle home paths or step construction.
"""

from __future__ import annotations

import hashlib
import shlex

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
    remote_marker: bool = True,
) -> AutomationStep:
    return AutomationStep(
        phase=phase,
        name=name,
        node=node,
        title=title,
        command=_with_remote_marker(phase, name, command) if remote_marker else command,
        timeout=timeout,
        warn_only=warn_only,
    )


def safe_name(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_").lower()


def stage_patch_lines(sources_path: str, patch_file: str, variable: str = "PATCH_TOP") -> list[str]:
    patch_zip = f"{sources_path}/{patch_file}"
    patch_dir = f"{STAGE}/patches/{safe_name(patch_file)}"
    return [
        f"test -s {shlex.quote(patch_zip)}",
        f"mkdir -p {patch_dir}",
        f"unzip -oq {shlex.quote(patch_zip)} -d {patch_dir}",
        patch_top_assignment(patch_dir, variable),
        f'echo "Detected patch top: ${variable}"',
    ]


def patch_top_assignment(patch_dir: str, variable: str = "PATCH_TOP") -> str:
    return (
        f"{variable}=$(find {patch_dir} -path '*/etc/config/inventory.xml' -type f "
        "-print | sed 's#/etc/config/inventory.xml##' | sort | head -1)\n"
        f"if test -z \"${variable}\"; then {variable}=$(find {patch_dir} -mindepth 1 -maxdepth 1 -type d | sort | head -1); fi\n"
        f"test -n \"${variable}\""
    )


def _with_remote_marker(phase: str, name: str, command: str) -> str:
    marker_dir = f"{STAGE}/oracle-auto/state/{safe_name(phase)}"
    marker = f"{marker_dir}/{safe_name(name)}.done"
    checksum = hashlib.sha256(command.encode("utf-8")).hexdigest()
    script = f"""# oracle-auto remote marker wrapper
set -euo pipefail
if sudo -n test -f {shlex.quote(marker)}; then
  if sudo -n grep -q {shlex.quote(checksum)} {shlex.quote(marker)}; then
    echo "Already completed remotely: {phase}:{name}"
    exit 0
  fi
  echo "Remote marker checksum changed; rerunning: {phase}:{name}"
fi
sudo -n mkdir -p {shlex.quote(marker_dir)}
{command}
printf '%s\\n' {shlex.quote(checksum)} | sudo -n tee {shlex.quote(marker)} >/dev/null
"""
    return "bash -lc " + shlex.quote(script)
