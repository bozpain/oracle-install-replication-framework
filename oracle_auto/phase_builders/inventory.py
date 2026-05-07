"""Remote inventory phase manual.

Inventory is read-only. It gathers OS, network, storage UUID, installer source,
and basic resource facts before deployment changes are made.
"""

from __future__ import annotations

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig
from oracle_auto.phase_builders.common import make_step


def inventory_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        make_step(
            "inventory",
            "collect_inventory",
            node,
            "Collect read-only remote inventory",
            _inventory_script(config),
            timeout=300,
            warn_only=True,
        )
        for node in config.all_nodes
    ]


def _inventory_script(config: AutomationConfig) -> str:
    lines = [
        "echo '[system]'",
        "hostname -f || true",
        "cat /etc/os-release || true",
        "uname -a || true",
        "echo '[cpu-memory]'",
        "lscpu || true",
        "free -m || true",
        "echo '[network]'",
        "ip -o addr show || true",
        "ip route || true",
        "cat /etc/resolv.conf || true",
        "echo '[dns]'",
        *[f"getent hosts {site.scan_name} || true" for site in config.sites if site.scan_name],
        "echo '[storage]'",
        "multipath -ll || true",
        "udevadm info --export-db | grep -E 'DM_UUID=|DEVNAME=' || true",
        "ls -l /dev/oracleasm 2>/dev/null || true",
        "echo '[installer-sources]'",
        f"ls -lh {config.installer.sources_path} 2>/dev/null || true",
    ]
    return shell_script("Collect remote inventory", lines)
