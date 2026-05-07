"""Precheck runner manual.

Precheck is the non-destructive gate before OS preparation. It validates SSH,
OS baseline, DNS resolver/SCAN behavior, installer source visibility, and ASM
disk DM_UUID visibility. It should fail early when the target cannot support the later
automation phases.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from oracle_auto.config import AutomationConfig, NodeConfig
from oracle_auto.executor import CommandResult, SSHExecutor
from oracle_auto.state import StateStore


@dataclass(frozen=True)
class PrecheckItem:
    host: str
    name: str
    status: str
    message: str
    command: str
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "host": self.host,
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass(frozen=True)
class Check:
    name: str
    command: str
    fail_message: str
    warn_only: bool = False
    timeout: int = 60


class PrecheckRunner:
    def __init__(
        self,
        config: AutomationConfig,
        executor: SSHExecutor,
        state: StateStore,
        resume: bool = True,
    ):
        self.config = config
        self.executor = executor
        self.state = state
        self.resume = resume

    def run(self) -> list[PrecheckItem]:
        results: list[PrecheckItem] = []
        for node in self.config.all_nodes:
            for check in self._checks_for(node):
                step = f"precheck:{node.host}:{check.name}"
                if self.resume and self.state.is_done(step):
                    results.append(
                        PrecheckItem(
                            host=node.host,
                            name=check.name,
                            status="PASS",
                            message="Already completed; use --no-resume to re-run.",
                            command=check.command,
                        )
                    )
                    continue

                self.state.mark_running(step)
                result = self.executor.run(node, check.command, timeout=check.timeout)
                item = self._to_precheck_item(check, result)
                results.append(item)

                if item.status == "FAIL":
                    self.state.mark_failed(step, item.to_dict())
                else:
                    self.state.mark_done(step, item.to_dict())

        return results

    def _checks_for(self, node: NodeConfig) -> list[Check]:
        sources = self.config.installer.sources_path
        package_manager = self.config.os.package_manager
        preinstall_package = self.config.os.preinstall_package
        checks = [
            Check(
                name="ssh_connectivity",
                command="printf ok",
                fail_message="Unable to execute command over SSH.",
                timeout=15,
            ),
            Check(
                name="os_release",
                command=(
                    "test -r /etc/os-release && "
                    ". /etc/os-release && "
                    f"test \"$ID\" = \"ol\" && test \"$VERSION_ID\" = \"{self.config.os.version}\" && "
                    "printf \"%s %s\" \"$ID\" \"$VERSION_ID\""
                ),
                fail_message=f"Target OS must be Oracle Linux {self.config.os.version}.",
            ),
            Check(
                name="kernel",
                command="uname -r",
                fail_message="Cannot read kernel version.",
            ),
            Check(
                name="package_manager",
                command=f"command -v {package_manager}",
                fail_message=f"Package manager is not available: {package_manager}",
            ),
            Check(
                name="oracle_yum_repo",
                command=f"{package_manager} repolist enabled",
                fail_message="Cannot read enabled Oracle Linux yum/dnf repositories.",
            ),
            Check(
                name="preinstall_package",
                command=f"{package_manager} list {preinstall_package}",
                fail_message=f"Cannot find package from enabled repo: {preinstall_package}",
            ),
            Check(
                name="dns_resolver_config",
                command=_resolver_check(self.config.dns.resolvers),
                fail_message="Target resolver config does not contain the configured DNS resolver yet.",
                warn_only=True,
            ),
            Check(
                name="public_hostname_resolve",
                command=f"getent hosts {shlex.quote(node.host)}",
                fail_message=f"Public hostname does not resolve: {node.host}",
                warn_only=True,
            ),
            Check(
                name="sources_path",
                command=f"test -d {shlex.quote(sources)} && test -r {shlex.quote(sources)} && ls -1 {shlex.quote(sources)} | head",
                fail_message=f"Installer source path is missing or unreadable: {sources}",
            ),
            Check(
                name="installer_zip_files",
                command=_installer_check(self.config),
                fail_message="One or more configured installer/patch ZIP files are missing or empty.",
            ),
            Check(
                name="u01_capacity",
                command="df -Pk /u01 | awk 'NR==2 {print $4}'",
                fail_message="/u01 is missing or capacity cannot be checked.",
            ),
            Check(
                name="oracle_user",
                command="id oracle",
                fail_message="OS user oracle does not exist yet; prepare-os will create it.",
                warn_only=True,
            ),
            Check(
                name="grid_user",
                command="id grid",
                fail_message="OS user grid does not exist yet; prepare-os will create it.",
                warn_only=True,
            ),
            Check(
                name="time_sync",
                command="chronyc tracking || timedatectl status",
                fail_message="Cannot verify chrony/timedatectl time sync status.",
                warn_only=True,
            ),
            Check(
                name="selinux_status",
                command="getenforce",
                fail_message="Cannot read SELinux status.",
                warn_only=True,
            ),
            Check(
                name="asm_disk_uuids_visible",
                command=_disk_check(self.config),
                fail_message="One or more configured ASM disk DM_UUID values are not visible to udev.",
            ),
        ]

        if self.config.install_type == "rac":
            checks.extend(
                [
                    Check(
                        name="hostname_fqdn",
                        command="hostname -f",
                        fail_message="Cannot resolve FQDN hostname.",
                    ),
                    Check(
                        name="scan_dns_resolve",
                        command=_scan_check(self.config),
                        fail_message="SCAN DNS name does not resolve from target DNS.",
                    ),
                    Check(
                        name="private_interconnect_hint",
                        command="ip -o addr show | awk '{print $2, $4}'",
                        fail_message="Cannot inspect network interfaces.",
                        warn_only=True,
                    ),
                ]
            )

        return checks

    @staticmethod
    def _to_precheck_item(check: Check, result: CommandResult) -> PrecheckItem:
        if result.ok:
            status = "SKIP" if result.skipped else "PASS"
            message = result.command if result.skipped else (_compact(result.stdout) or "OK")
        elif check.warn_only:
            status = "WARN"
            message = check.fail_message
        else:
            status = "FAIL"
            message = check.fail_message

        detail = result.stderr if result.stderr else result.stdout
        if not result.ok and detail:
            message = f"{message} ({_compact(detail)})"

        return PrecheckItem(
            host=result.host,
            name=check.name,
            status=status,
            message=message,
            command=result.command,
            stdout=result.stdout,
            stderr=result.stderr,
        )


def _resolver_check(resolvers: list[str]) -> str:
    parts = [f"grep -q '^nameserver[[:space:]]\\+{shlex.quote(resolver)}' /etc/resolv.conf" for resolver in resolvers]
    return " || ".join(parts)


def _installer_check(config: AutomationConfig) -> str:
    files = [config.installer.grid_zip, config.installer.db_zip]
    if config.installer.opatch_zip:
        files.append(config.installer.opatch_zip)
    files.extend(patch.file for patch in config.installer.patches)
    return " && ".join(
        f"test -s {shlex.quote(config.installer.sources_path + '/' + file)}" for file in files
    )


def _disk_check(config: AutomationConfig) -> str:
    return " && ".join(
        f"udevadm info --export-db | grep -q {shlex.quote('DM_UUID=' + dm_uuid)}"
        for dm_uuid in config.asm.all_dm_uuids
    )


def _scan_check(config: AutomationConfig) -> str:
    scans = [site.scan_name for site in config.sites if site.scan_name]
    return " && ".join(f"getent hosts {shlex.quote(scan)}" for scan in scans)


def _compact(value: str, limit: int = 140) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
