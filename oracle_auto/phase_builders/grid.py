"""Grid Infrastructure phase manual.

Builds Grid Infrastructure response files and root script execution steps. RAC
and single-GI share this module because both use GI and ASM.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig, SiteConfig
from oracle_auto.phase_builders.common import GRID_BASE, STAGE, make_step
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
        f"mkdir -p {STAGE}/responses",
        _scan_dns_guard(site),
        _hosts_guard(config),
        f"test -x {GRID_BASE}/gridSetup.sh || sudo -iu grid unzip -oq {shlex.quote(config.installer.sources_path)}/{shlex.quote(config.installer.grid_zip)} -d {GRID_BASE}",
        f"cat > {STAGE}/responses/grid-{site.name}.rsp <<'EOF'\n{response}\nEOF",
        f"chown grid:oinstall {STAGE}/responses/grid-{site.name}.rsp",
        f"sudo -iu grid {GRID_BASE}/gridSetup.sh -silent -waitforcompletion -responseFile {STAGE}/responses/grid-{site.name}.rsp -ignorePrereqFailure",
    ]
    return shell_script(f"Install Grid Infrastructure for {site.name}", lines)


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
