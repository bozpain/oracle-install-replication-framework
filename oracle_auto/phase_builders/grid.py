"""Grid Infrastructure phase manual.

Builds Grid Infrastructure response files and root script execution steps. RAC
and single-GI share this module because both use GI and ASM.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig, SiteConfig
from oracle_auto.phase_builders.common import GRID_BASE, STAGE, make_step, stage_patch_lines
from oracle_auto.phase_builders.storage import afd_label_command, asm_entries
from oracle_auto.response_files.grid import grid_response


def install_grid_steps(config: AutomationConfig) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for site in config.sites:
        first = site.nodes[0]
        steps.append(
            make_step(
                "install-grid",
                f"install_grid_{site.name}",
                first,
                f"Install Grid Infrastructure for {site.name}",
                _install_grid_script(config, site),
                timeout=7200,
            )
        )
        for node in site.nodes:
            steps.append(
                make_step(
                    "install-grid",
                    f"root_scripts_{site.name}_{node.short_name}",
                    node,
                    f"Run Grid root scripts for {node.host}",
                    _grid_root_script(),
                    timeout=1800,
                )
            )
    return steps


def _install_grid_script(config: AutomationConfig, site: SiteConfig) -> str:
    response = grid_response(config, site)
    lines = [
        _asm_password_export(config),
        "export CV_ASSUME_DISTID=OL7",
        f"mkdir -p {STAGE}/responses",
        "umask 077",
        _scan_dns_guard(site),
        _hosts_guard(config),
        f"test -x {GRID_BASE}/gridSetup.sh || sudo -iu grid unzip -oq {shlex.quote(config.installer.sources_path)}/{shlex.quote(config.installer.grid_zip)} -d {GRID_BASE}",
        *_grid_patch_stage_lines(config),
        *_initial_afd_label_lines(config),
        f"cat > {STAGE}/responses/grid-{site.name}.rsp <<EOF\n{response}\nEOF",
        f"chown grid:oinstall {STAGE}/responses/grid-{site.name}.rsp",
        f"chmod 600 {STAGE}/responses/grid-{site.name}.rsp",
        f"sudo -iu grid {GRID_BASE}/gridSetup.sh -silent -waitforcompletion -responseFile {STAGE}/responses/grid-{site.name}.rsp{_grid_patch_arg(config)} -ignorePrereqFailure",
    ]
    return shell_script(f"Install Grid Infrastructure for {site.name}", lines)


def _grid_patch_stage_lines(config: AutomationConfig) -> list[str]:
    if config.installer.grid_patch is None:
        return []
    return stage_patch_lines(config.installer.sources_path, config.installer.grid_patch.file, "GRID_PATCH_TOP")


def _grid_patch_arg(config: AutomationConfig) -> str:
    if config.installer.grid_patch is None:
        return ""
    return ' -applyRU "$GRID_PATCH_TOP"'


def _asm_password_export(config: AutomationConfig) -> str:
    return (
        f'ASMSNMP_PASSWORD="${{{config.secrets.asmsnmp_password_env}:?'
        f'Set {config.secrets.asmsnmp_password_env} on target before running install-grid}}"\n'
        "export ASMSNMP_PASSWORD"
    )


def _initial_afd_label_lines(config: AutomationConfig) -> list[str]:
    initial_group = "OCR" if config.install_type == "rac" else "DATA"
    entries = [(label, path) for label, path, group, _disk in asm_entries(config) if group == initial_group]
    lines = [
        "echo 'Label initial Grid Infrastructure diskgroup with ASMFD before gridSetup.sh'",
        f"export ORACLE_HOME={GRID_BASE}",
        "export ORACLE_BASE=/u01/app/grid",
        f"test -x {GRID_BASE}/bin/asmcmd",
    ]
    for label, path in entries:
        lines.append(f"test -b {shlex.quote(path)}")
        lines.append(afd_label_command(label, path))
    lines.append(f"{GRID_BASE}/bin/asmcmd afd_lslbl || true")
    return lines


def _grid_root_script() -> str:
    lines = [
        "test -x /u01/app/oraInventory/orainstRoot.sh && /u01/app/oraInventory/orainstRoot.sh || true",
        f"test -x {GRID_BASE}/root.sh",
        f"if sudo -iu grid {GRID_BASE}/bin/crsctl check crs >/dev/null 2>&1; then echo 'Grid appears active; skipping root.sh rerun.'; else {GRID_BASE}/root.sh; fi",
        f"sudo -iu grid {GRID_BASE}/bin/crsctl check crs || true",
        "sudo -iu grid asmcmd lsdg || true",
    ]
    return shell_script("Run Grid root scripts", lines)


def _scan_dns_guard(site: SiteConfig) -> str:
    if not site.scan_name:
        return "true"
    return f"getent hosts {shlex.quote(site.scan_name)}"


def _hosts_guard(config: AutomationConfig) -> str:
    checks: list[str] = []
    for node in config.all_nodes:
        checks.append(f"grep -qw -- {shlex.quote(node.host)} /etc/hosts")
        if node.private_ip:
            checks.append(f"grep -qw -- {shlex.quote(node.private_hostname)} /etc/hosts")
        if node.vip_ip:
            checks.append(f"grep -qw -- {shlex.quote(node.vip_hostname)} /etc/hosts")
    return " && ".join(checks)
