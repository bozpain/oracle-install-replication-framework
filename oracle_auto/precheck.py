"""Precheck runner manual.

Precheck is the non-destructive gate before OS preparation. It validates SSH,
OS baseline, DNS resolver/SCAN behavior, installer source visibility, and ASM
disk path/DM_UUID visibility. Only SCAN is checked through DNS; public, private,
and VIP names are treated as `/etc/hosts` content managed by prepare-os.
"""

from __future__ import annotations

import shlex
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from oracle_auto.config import AutomationConfig, NodeConfig
from oracle_auto.executor import CommandResult, SSHExecutor
from oracle_auto.phase_builders.storage import asmlib_kernel_check_command
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
    timeout: int | None = 60
    stop_host_on_fail: bool = False


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
        if len(self.config.all_nodes) > 1:
            with ThreadPoolExecutor(max_workers=len(self.config.all_nodes)) as pool:
                host_results = pool.map(self._run_node_checks, self.config.all_nodes)
            return [item for items in host_results for item in items]
        return self._run_node_checks(self.config.all_nodes[0]) if self.config.all_nodes else []

    def _run_node_checks(self, node: NodeConfig) -> list[PrecheckItem]:
        results: list[PrecheckItem] = []
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

            print(f"RUN   precheck:{node.host}:{check.name}", flush=True)
            self.state.mark_running(step)
            result = self.executor.run(node, check.command, timeout=check.timeout)
            item = self._to_precheck_item(check, result)
            results.append(item)

            if item.status == "FAIL":
                self.state.mark_failed(step, item.to_dict())
            elif item.status == "WARN":
                self.state.mark_warning(step, item.to_dict())
            else:
                self.state.mark_done(step, item.to_dict())

            if item.status == "FAIL" and check.stop_host_on_fail:
                print(f"STOP  precheck:{node.host}:{check.name} failed; skipping remaining checks for this host.", flush=True)
                break

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
                stop_host_on_fail=True,
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
                timeout=None,
            ),
            Check(
                name="preinstall_package",
                command=f"rpm -q {preinstall_package} || {package_manager} list {preinstall_package}",
                fail_message=f"Cannot find package from enabled repo: {preinstall_package}",
                timeout=None,
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
                fail_message="One or more configured installer/patch ZIP files or ASMLIB RPMs are missing or empty.",
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
                name="swap_capacity",
                command="awk '/SwapTotal/ {exit !($2 >= 524288)}' /proc/meminfo",
                fail_message="Swap is below 512 MB; prepare-os will create a 1 GiB /swapfile before installation.",
                warn_only=True,
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
                name="asm_disks_visible",
                command=_disk_check(self.config, node),
                fail_message="One or more configured ASM disk source values are not visible.",
            ),
            Check(
                name="storage_mode_detection",
                command=_storage_mode_check(self.config),
                fail_message="Cannot determine a compatible ASM storage mode.",
            ),
            Check(
                name="asm_disk_signatures",
                command=_disk_signature_check(self.config, node),
                fail_message="One or more ASM candidate disks already have filesystem signatures.",
                warn_only=True,
            ),
            Check(
                name="asm_disk_sizes",
                command=_disk_size_check(self.config, node),
                fail_message="Cannot read one or more ASM candidate disk sizes.",
                warn_only=True,
            ),
        ]
        if self.config.asm.storage_mode == "asmlibv3":
            checks[3:3] = [
                Check(
                    name="asmlib_kernel_interface",
                    command=asmlib_kernel_check_command(),
                    fail_message="ASMLIB v3 requires UEK R7+ (5.15+) or an oracleasm kernel driver.",
                ),
            ]
            package_index = next(index for index, check in enumerate(checks) if check.name == "dns_resolver_config")
            checks[package_index:package_index] = [
                Check(
                    name="asmlib_packages",
                    command=f"{package_manager} list oracleasm-support && ({package_manager} list oracleasmlib || echo 'oracleasmlib will be installed from local RPM in configured sources_path')",
                    fail_message="Cannot find Oracle ASMLIB v3 packages from enabled repositories; prepare-os can enable ol8_addons and install oracleasmlib from local RPM in configured sources_path.",
                    warn_only=True,
                    timeout=None,
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
            status = "DRYRUN" if result.skipped else "PASS"
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
    checks = [
        f"test -s {shlex.quote(config.installer.sources_path + '/' + file)}" for file in files
    ]
    checks.append(_asmlib_rpm_check(config))
    return " && ".join(checks)


def _asmlib_rpm_check(config: AutomationConfig) -> str:
    cases = " ".join(
        f"{shlex.quote(arch)}) test -s {shlex.quote(config.installer.sources_path + '/' + rpm)} ;;"
        for arch, rpm in sorted(config.os.asmlib_rpms.items())
    )
    return f"arch=$(uname -m); case \"$arch\" in {cases} *) echo \"Unsupported ASMLIB architecture: $arch\" >&2; exit 1 ;; esac"


def _installer_content_check(config: AutomationConfig) -> str:
    sources = config.installer.sources_path
    checks = [
        f"unzip -l {shlex.quote(sources + '/' + config.installer.grid_zip)} | grep 'gridSetup.sh' >/dev/null",
        f"unzip -l {shlex.quote(sources + '/' + config.installer.db_zip)} | grep 'runInstaller' >/dev/null",
    ]
    if config.installer.opatch_zip:
        checks.append(f"unzip -l {shlex.quote(sources + '/' + config.installer.opatch_zip)} | grep 'OPatch/' >/dev/null")
    for patch in config.installer.patches:
        checks.append(f"unzip -l {shlex.quote(sources + '/' + patch.file)} | awk 'NR > 3 {{print $4}}' | grep '^[0-9][0-9]*/' >/dev/null")
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


def _disk_check(config: AutomationConfig, node: NodeConfig | None = None) -> str:
    commands: list[str] = []
    site = config.site_for_node(node) if node else None

    for disk in config.asm.all_disks:
        path = disk.path_for(site_name=site.name if site else None, node_host=node.host if node else None)
        commands.append(
            f"test -e {shlex.quote(path)} "
            f"&& resolved=$(readlink -f {shlex.quote(path)}) "
            f"&& test -b \"$resolved\""
        )

    return " && ".join(commands)


def _storage_mode_check(config: AutomationConfig) -> str:
    requires_multipath = any(disk.uuid for disk in config.asm.all_disks)
    no_multipath_message = (
        "virtual-machine/direct-asmlib: no active multipath output; "
        "using persistent by-id/ID_SERIAL/ID_WWN devices directly"
    )
    lines = [
        "probe=$(mktemp /tmp/oracle-auto-multipath-precheck.XXXXXX)",
        "trap 'rm -f \"$probe\"' EXIT",
        "if command -v multipath >/dev/null 2>&1 && multipath -ll >\"$probe\" 2>/dev/null && test -s \"$probe\"; then",
        "  echo 'physical/multipath-udev: active multipath devices detected'",
        "  cat \"$probe\"",
        "  exit 0",
        "fi",
    ]
    if requires_multipath:
        lines.extend(
            [
                "echo 'ASM config uses DM_UUID/multipath disks but no active multipath output was detected.' >&2",
                "exit 1",
            ]
        )
    else:
        lines.extend(
            [
                f"echo {shlex.quote(no_multipath_message)}",
                "exit 0",
            ]
        )
    return "\n".join(lines)


def _hosts_file_check(config: AutomationConfig) -> str:
    return " && ".join(
        f"grep -qw -- {shlex.quote(hostname)} /etc/hosts"
        for hostname in _local_hostnames(config)
    )


def _disk_signature_check(config: AutomationConfig, node: NodeConfig | None = None) -> str:
    commands = []
    site = config.site_for_node(node) if node else None

    for disk in config.asm.all_disks:
        path = disk.path_for(site_name=site.name if site else None, node_host=node.host if node else None)
        commands.append(
            f'resolved=$(readlink -f {shlex.quote(path)}) && '
            'test -n "$resolved" && '
            'test -z "$(sudo -n wipefs -n "$resolved" 2>/dev/null | awk \'NR>1\')"'
        )

    return " && ".join(commands)


def _disk_size_check(config: AutomationConfig, node: NodeConfig | None = None) -> str:
    commands = []
    site = config.site_for_node(node) if node else None

    for disk in config.asm.all_disks:
        path = disk.path_for(site_name=site.name if site else None, node_host=node.host if node else None)
        commands.append(
            f'resolved=$(readlink -f {shlex.quote(path)}) && '
            f'printf "{path} " && '
            'sudo -n blockdev --getsize64 "$resolved"'
        )

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
