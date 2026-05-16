"""Shared phase builder manual.

Keep cross-phase constants and tiny helpers here. Phase modules should import
these helpers instead of redefining Oracle home paths or step construction.
"""

from __future__ import annotations

import hashlib
import shlex

from oracle_auto.automation import AutomationStep
from oracle_auto.config import NodeConfig

INVENTORY_LOCATION = "/u01/app/oraInventory"
GRID_BASE = "/u01/app/19.0.0/grid"
GRID_BASE_DIR = "/u01/app/grid"
ORACLE_BASE = "/u01/app/oracle"
DB_HOME = "/u01/app/oracle/product/19.0.0/dbhome_1"
STAGE = "/u01/stage"


def oracle_user_group_lines() -> list[str]:
    return [
        'for group in oinstall dba oper backupdba dgdba kmdba racdba asmadmin asmdba asmoper; do getent group "$group" >/dev/null || groupadd "$group"; done',
        'id grid >/dev/null 2>&1 || useradd -g oinstall -G asmadmin,asmdba,asmoper,dba,racdba grid',
        'id oracle >/dev/null 2>&1 || useradd -g oinstall -G dba,oper,backupdba,dgdba,kmdba,racdba,asmdba oracle',
        "usermod -aG asmadmin,asmdba,asmoper,dba,racdba grid",
        "usermod -aG dba,oper,backupdba,dgdba,kmdba,racdba,asmdba oracle",
        "id grid",
        "id oracle",
    ]


def inventory_pointer_lines() -> list[str]:
    return [
        f"mkdir -p {INVENTORY_LOCATION}",
        f"chown -R grid:oinstall {INVENTORY_LOCATION}",
        f"chmod -R 775 {INVENTORY_LOCATION}",
        "cat > /etc/oraInst.loc <<'EOF'\n"
        f"inventory_loc={INVENTORY_LOCATION}\n"
        "inst_group=oinstall\n"
        "EOF",
        "chown root:oinstall /etc/oraInst.loc",
        "chmod 664 /etc/oraInst.loc",
    ]


def oracle_home_inventory_pointer_lines(home: str, owner: str) -> list[str]:
    quoted_home = shlex.quote(home)
    quoted_pointer = shlex.quote(f"{home}/oraInst.loc")
    return [
        f"if test -d {quoted_home}; then",
        f"  cat > {quoted_pointer} <<'EOF'\n"
        f"inventory_loc={INVENTORY_LOCATION}\n"
        "inst_group=oinstall\n"
        "EOF",
        f"  chown {shlex.quote(owner)}:oinstall {quoted_pointer}",
        f"  chmod 664 {quoted_pointer}",
        "fi",
    ]


def make_step(
    phase: str,
    name: str,
    node: NodeConfig,
    title: str,
    command: str,
    timeout: int | None,
    warn_only: bool = False,
    remote_marker: bool = True,
    force_rerun: bool = False,
) -> AutomationStep:
    return AutomationStep(
        phase=phase,
        name=name,
        node=node,
        title=title,
        command=_with_remote_marker(phase, name, command) if remote_marker else command,
        timeout=timeout,
        warn_only=warn_only,
        force_rerun=force_rerun,
    )


def safe_name(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_").lower()


def stage_patch_lines(
    sources_path: str,
    patch_file: str,
    variable: str = "PATCH_TOP",
    patch_id: str | None = None,
) -> list[str]:
    patch_zip = f"{sources_path}/{patch_file}"
    patch_dir = f"{sources_path}/{patch_id}" if patch_id else sources_path
    cleanup_lines = [f"rm -rf {shlex.quote(patch_dir)}"] if patch_id else []
    return [
        f"test -s {shlex.quote(patch_zip)}",
        f"mkdir -p {shlex.quote(sources_path)}",
        f"chmod a+rx {shlex.quote(sources_path)}",
        *cleanup_lines,
        f"unzip -oq {shlex.quote(patch_zip)} -d {shlex.quote(sources_path)}",
        f"chmod -R a+rX {shlex.quote(patch_dir)}",
        patch_top_assignment(patch_dir, variable, prefer_self=patch_id is not None),
        f"test -d \"${variable}\"",
        f'echo "Detected patch top: ${variable}"',
    ]


def ensure_swap_lines() -> list[str]:
    return [
        "echo 'Ensuring at least 512 MiB swap for Oracle installer'",
        "swap_kb=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)",
        'if test "${swap_kb:-0}" -lt 524288; then',
        "  test -f /swapfile || fallocate -l 1G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none",
        "  chmod 600 /swapfile",
        "  if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then mkswap /swapfile >/dev/null; swapon /swapfile; fi",
        "  grep -q '^/swapfile[[:space:]]' /etc/fstab || printf '/swapfile none swap sw 0 0\\n' >> /etc/fstab",
        "fi",
        "awk '/SwapTotal/ {print; exit !($2 >= 524288)}' /proc/meminfo",
    ]


def install_asmlib_lines(package_manager: str, sources_path: str, asmlib_rpms: dict[str, str]) -> list[str]:
    pm = shlex.quote(package_manager)
    rpm_path_cases = "\n".join(
        f"  {arch}) asmlib_rpm={shlex.quote(f'{sources_path}/{rpm}')} ;;"
        for arch, rpm in sorted(asmlib_rpms.items())
    )
    return [
        f"if ! rpm -q oracleasm-support >/dev/null 2>&1; then {pm} install -y oracleasm-support || (if grep -Rqs '^\\[ol8_addons\\]' /etc/yum.repos.d; then {pm} config-manager --set-enabled ol8_addons 2>/dev/null || sed -i '/^\\[ol8_addons\\]/,/^\\[/{{s/^enabled=.*/enabled=1/}}' /etc/yum.repos.d/*.repo; fi; {pm} install -y oracleasm-support); fi",
        "if ! rpm -q oracleasmlib >/dev/null 2>&1; then\n"
        "  arch=$(uname -m)\n"
        "  case \"$arch\" in\n"
        f"{rpm_path_cases}\n"
        "    *) echo \"Unsupported ASMLIB architecture: $arch\" >&2; exit 1 ;;\n"
        "  esac\n"
        "  test -s \"$asmlib_rpm\"\n"
        f"  {pm} install -y \"$asmlib_rpm\"\n"
        "fi",
        "rpm -q oracleasm-support oracleasmlib",
    ]


def patch_top_assignment(
    patch_dir: str,
    variable: str = "PATCH_TOP",
    patch_id: str | None = None,
    prefer_self: bool = False,
) -> str:
    preferred = ""
    if prefer_self:
        preferred += f"if test -d {patch_dir}; then {variable}={patch_dir}; fi\n"
    if patch_id:
        preferred += f"if test -d {patch_dir}/{shlex.quote(str(patch_id))}; then {variable}={patch_dir}/{shlex.quote(str(patch_id))}; fi\n"
    return (
        f"{variable}=\n"
        f"{preferred}"
        f"if test -z \"${variable}\"; then {variable}=$(find {patch_dir} -path '*/etc/config/inventory.xml' -type f "
        "-print | sed 's#/etc/config/inventory.xml##' | sort | head -1); fi\n"
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
  if test "${{ORACLE_AUTO_NO_REMOTE_RESUME:-0}}" = "1"; then
    echo "Remote marker bypass requested; rerunning: {phase}:{name}"
  elif sudo -n grep -q {shlex.quote(checksum)} {shlex.quote(marker)}; then
    echo "Already completed remotely: {phase}:{name}"
    exit 0
  else
    echo "Remote marker checksum changed; rerunning: {phase}:{name}"
  fi
fi
sudo -n mkdir -p {shlex.quote(marker_dir)}
{command}
printf '%s\\n' {shlex.quote(checksum)} | sudo -n tee {shlex.quote(marker)} >/dev/null
"""
    return "bash -lc " + shlex.quote(script)
