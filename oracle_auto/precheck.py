"""Precheck runner manual.

Precheck is the non-destructive gate before OS preparation. It validates SSH,
OS baseline, DNS resolver/SCAN behavior, installer source visibility, and ASM
disk DM_UUID visibility. Only SCAN is checked through DNS; public, private, and
VIP names are treated as `/etc/hosts` content managed by prepare-os.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from oracle_auto.config import AutomationConfig, NodeConfig
from oracle_auto.executor import CommandResult, SSHExecutor
from oracle_auto.secrets import redact
from oracle_auto.state import StateBackend


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
        state: StateBackend,
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
                name="hosts_file_entries",
                command=_hosts_file_check(self.config),
                fail_message="Configured public/private/VIP host entries are not all present in /etc/hosts yet; prepare-os will write them.",
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
                name="installer_zip_integrity",
                command=_installer_integrity_check(self.config),
                fail_message="One or more configured installer/patch ZIP files failed unzip integrity testing.",
                timeout=300,
            ),
            Check(
                name="installer_zip_contents",
                command=_installer_content_check(self.config),
                fail_message="Configured installer ZIP files do not contain expected Oracle installer entry points.",
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
                name="sudo_available",
                command="command -v sudo && sudo -n true",
                fail_message="sudo is not available for root automation or requires interaction.",
            ),
            Check(
                name="sudo_user_switch",
                command="id grid && id oracle && sudo -iu grid true && sudo -iu oracle true",
                fail_message="Cannot switch non-interactively to separated grid/oracle users yet; prepare-os creates users before this must pass.",
                warn_only=True,
            ),
            Check(
                name="secret_environment",
                command=_secret_env_check(self.config),
                fail_message="One or more required Oracle automation secret environment variables are missing on target.",
            ),
            Check(
                name="asm_disk_uuids_visible",
                command=_disk_check(self.config),
                fail_message="One or more configured ASM disk UUID/path values are not visible to udev.",
            ),
            Check(
                name="multipath_health",
                command="command -v multipath && multipath -ll",
                fail_message="multipath command is unavailable or no multipath output is visible.",
                warn_only=True,
            ),
            Check(
                name="asm_disk_signatures",
                command=_disk_signature_check(self.config),
                fail_message="One or more ASM candidate disks already have filesystem signatures.",
                warn_only=True,
            ),
            Check(
                name="asm_disk_sizes",
                command=_disk_size_check(self.config),
                fail_message="Cannot read one or more ASM candidate disk sizes.",
                warn_only=True,
            ),
            Check(
                name="oracleasm_symlink_collisions",
                command=_symlink_collision_check(self.config),
                fail_message="One or more /dev/oracleasm symlink names already exist and are not block devices.",
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
                        name="scan_dns_record_count",
                        command=_scan_count_check(self.config),
                        fail_message="SCAN DNS record count could not be inspected.",
                        warn_only=True,
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
            message=redact(message),
            command=redact(result.command),
            stdout=redact(result.stdout),
            stderr=redact(result.stderr),
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


def _installer_integrity_check(config: AutomationConfig) -> str:
    return " && ".join(
        f"unzip -t {shlex.quote(config.installer.sources_path + '/' + file)} >/dev/null"
        for file in _installer_files(config)
    )


def _installer_content_check(config: AutomationConfig) -> str:
    sources = config.installer.sources_path
    checks = [
        f"unzip -l {shlex.quote(sources + '/' + config.installer.grid_zip)} | grep -q 'gridSetup.sh'",
        f"unzip -l {shlex.quote(sources + '/' + config.installer.db_zip)} | grep -q 'runInstaller'",
    ]
    if config.installer.opatch_zip:
        checks.append(f"unzip -l {shlex.quote(sources + '/' + config.installer.opatch_zip)} | grep -q 'OPatch/'")
    for patch in config.installer.patches:
        checks.append(f"unzip -l {shlex.quote(sources + '/' + patch.file)} | awk 'NR > 3 {{print $4}}' | grep -q '^[0-9][0-9]*/'")
    return " && ".join(checks)


def _installer_files(config: AutomationConfig) -> list[str]:
    files = [config.installer.grid_zip, config.installer.db_zip]
    if config.installer.opatch_zip:
        files.append(config.installer.opatch_zip)
    files.extend(patch.file for patch in config.installer.patches)
    return files


def _secret_env_check(config: AutomationConfig) -> str:
    env_names = [
        config.secrets.sys_password_env,
        config.secrets.system_password_env,
        config.secrets.asmsnmp_password_env,
    ]
    if config.standby_site:
        env_names.append(config.secrets.dg_password_env)
    source_profile = (
        "set -a; "
        "test ! -r /etc/oracle-auto/secrets.env || . /etc/oracle-auto/secrets.env; "
        ". /etc/profile >/dev/null 2>&1 || true; "
        "for f in /etc/profile.d/*.sh; do . \"$f\" >/dev/null 2>&1 || true; done; "
        "set +a"
    )
    checks = " && ".join(f"test -n \"${{{name}:-}}\"" for name in env_names)
    script = f"{source_profile}; {checks}"
    return "sudo -n bash -lc " + shlex.quote(script)


def _disk_check(config: AutomationConfig) -> str:
    commands: list[str] = []
    for disk in config.asm.all_disks:
        if disk.uuid:
            commands.append(f"udevadm info --export-db | grep -q {shlex.quote('DM_UUID=' + disk.dm_uuid)}")
        elif disk.path:
            commands.append(f"test -b {shlex.quote(disk.path)}")
    return " && ".join(commands)


def _hosts_file_check(config: AutomationConfig) -> str:
    return " && ".join(
        f"grep -qw -- {shlex.quote(hostname)} /etc/hosts"
        for hostname in _local_hostnames(config)
    )


def _disk_signature_check(config: AutomationConfig) -> str:
    commands = []
    for disk in config.asm.all_disks:
        if disk.uuid:
            commands.append(
                "device=$(udevadm info --export-db | awk "
                f"{shlex.quote('/DM_UUID=' + disk.dm_uuid + '/{found=1} found && /^N: /{print \"/dev/\"$2; exit}')} ); "
                "test -n \"$device\" && test -z \"$(sudo -n wipefs -n \"$device\" 2>/dev/null | awk 'NR>1')\""
            )
        elif disk.path:
            commands.append(f"test -z \"$(sudo -n wipefs -n {shlex.quote(disk.path)} 2>/dev/null | awk 'NR>1')\"")
    return " && ".join(commands)


def _symlink_collision_check(config: AutomationConfig) -> str:
    paths: list[str] = []
    for group, disks in (
        ("OCR", config.asm.ocr_disks),
        ("DATA", config.asm.data_disks),
        ("RECO", config.asm.reco_disks),
    ):
        paths.extend(disk.symlink_path(group, index) for index, disk in enumerate(disks, start=1))
    return " && ".join(f"test ! -e {shlex.quote(path)} || test -b {shlex.quote(path)}" for path in paths)


def _disk_size_check(config: AutomationConfig) -> str:
    commands = []
    for disk in config.asm.all_disks:
        if disk.uuid:
            commands.append(
                "device=$(udevadm info --export-db | awk "
                f"{shlex.quote('/DM_UUID=' + disk.dm_uuid + '/{found=1} found && /^N: /{print \"/dev/\"$2; exit}')} ); "
                f"test -n \"$device\" && printf '{disk.dm_uuid} ' && sudo -n blockdev --getsize64 \"$device\""
            )
        elif disk.path:
            commands.append(f"printf '{shlex.quote(disk.path)} ' && sudo -n blockdev --getsize64 {shlex.quote(disk.path)}")
    return " && ".join(commands)


def _scan_check(config: AutomationConfig) -> str:
    scans = [site.scan_name for site in config.sites if site.scan_name]
    return " && ".join(f"getent hosts {shlex.quote(scan)}" for scan in scans)


def _scan_count_check(config: AutomationConfig) -> str:
    scans = [site.scan_name for site in config.sites if site.scan_name]
    return " && ".join(
        f"printf '%s ' {shlex.quote(scan)}; getent ahosts {shlex.quote(scan)} | awk '{{print $1}}' | sort -u | wc -l"
        for scan in scans
    )


def _local_hostnames(config: AutomationConfig) -> list[str]:
    names: list[str] = []
    for node in config.all_nodes:
        names.append(node.host)
        if node.private_ip:
            names.append(node.private_hostname)
        if node.vip_ip:
            names.append(node.vip_hostname)
    return names


def _compact(value: str, limit: int = 140) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
