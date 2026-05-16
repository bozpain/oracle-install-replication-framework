"""Diagnostics and lab cleanup phase manual.

Diagnostics gather remote facts for troubleshooting. Lab cleanup is intentionally
limited to framework-generated transient files; it does not remove Oracle homes,
ASM diskgroups, databases, ASMLIB labels, or patches.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig, NodeConfig
from oracle_auto.phase_builders.common import GRID_BASE, STAGE, make_step


def collect_diagnostics_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        make_step(
            "collect-diagnostics",
            "collect_diagnostics",
            node,
            "Collect Oracle automation diagnostics",
            _collect_diagnostics_script(config, node),
            timeout=600,
            warn_only=True,
        )
        for node in config.all_nodes
    ]


def cleanup_lab_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        make_step(
            "cleanup-lab",
            "cleanup_framework_artifacts",
            node,
            "Clean limited framework-generated lab artifacts",
            _cleanup_lab_script(),
            timeout=300,
        )
        for node in config.all_nodes
    ]


def _collect_diagnostics_script(config: AutomationConfig, node: NodeConfig) -> str:
    site = config.site_for_node(node)
    disk_args = " ".join(
        shlex.quote(disk.path_for(site_name=site.name, node_host=node.host))
        for disk in config.asm.all_disks
    )
    lines = [
        f"mkdir -p {STAGE}/diagnostics",
        f"(date; hostname -f; uname -a; cat /etc/os-release) > {STAGE}/diagnostics/system.txt",
        f"(ip addr; ip route; cat /etc/resolv.conf; cat /etc/hosts) > {STAGE}/diagnostics/network.txt",
        f"(multipath -ll || true; udevadm info --export-db | grep -E 'DM_UUID=|ID_SERIAL=|ID_WWN=' || true; oracleasm status || true; oracleasm listdisks || true; for path in {disk_args}; do printf '%s -> ' \"$path\"; readlink -f \"$path\"; wipefs -n \"$path\" || true; done) > {STAGE}/diagnostics/storage.txt",
        f"(systemctl status chronyd --no-pager || true; chronyc sources || true; getenforce || true) > {STAGE}/diagnostics/os-services.txt",
        f"(sudo -iu grid {GRID_BASE}/bin/crsctl check crs || true; sudo -iu grid asmcmd lsdg || true) > {STAGE}/diagnostics/grid-asm.txt",
        f"tar -czf {STAGE}/diagnostics/oracle-auto-diagnostics-$(hostname -s).tgz -C {STAGE} diagnostics",
        f"ls -lh {STAGE}/diagnostics",
    ]
    return shell_script("Collect diagnostics", lines)


def _cleanup_lab_script() -> str:
    lines = [
        "awk '/# BEGIN ORACLE-AUTO HOSTS/{skip=1} /# END ORACLE-AUTO HOSTS/{skip=0; next} !skip{print}' /etc/hosts > /etc/hosts.oracle-auto.rollback && mv /etc/hosts.oracle-auto.rollback /etc/hosts",
        "if ls -1t /etc/resolv.conf.oracle-auto.bak.* >/dev/null 2>&1; then cp -p $(ls -1t /etc/resolv.conf.oracle-auto.bak.* | head -1) /etc/resolv.conf; fi",
        "if test -f /etc/chrony.conf; then awk '/# BEGIN ORACLE-AUTO CHRONY/{skip=1} /# END ORACLE-AUTO CHRONY/{skip=0; next} !skip{print}' /etc/chrony.conf | sed 's/^# oracle-auto disabled //' > /etc/chrony.conf.oracle-auto.rollback && mv /etc/chrony.conf.oracle-auto.rollback /etc/chrony.conf && systemctl restart chronyd || true; fi",
        f"rm -rf {STAGE}/installer-checks {STAGE}/diagnostics",
        f"rm -rf {STAGE}/oracle-auto/state",
        f"mkdir -p {STAGE}/oracle-auto/state/cleanup_lab",
        "echo 'Limited framework rollback complete. Oracle homes, databases, ASM labels, and diskgroups were not removed.'",
    ]
    return shell_script("Limited framework rollback", lines)
