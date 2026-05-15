"""Configuration schema and validation manual.

This module is the contract for every automation phase. Keep all deployment
decisions here first, then let runners consume typed dataclasses instead of raw
JSON/YAML. The intended operator workflow is:

1. Fill one deployment config for `single-gi` or `rac`.
2. Provide public IPs, private IPs, RAC VIP IPs, SCAN DNS names, ASM disk DM_UUIDs, and installer ZIPs.
3. Let the framework derive `-priv` and `-vip` hostnames, validate topology, and
   drive all later commands from this normalized model.

Only SCAN names are expected to resolve from DNS. Public, private, and VIP names
are written to `/etc/hosts` during OS preparation and are validated as local host
file entries instead of DNS records.
"""

from __future__ import annotations

import json
import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


VALID_INSTALL_TYPES = {"single-gi", "rac"}
VALID_DATAGUARD_METHODS = {"manual", "broker"}
DEFAULT_NTP_SERVERS = ["192.168.113.41", "192.168.115.41"]


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SSHConfig:
    user: str = "root"
    port: int = 22
    key_file: str | None = None
    connect_timeout: int = 10
    server_alive_interval: int = 30
    server_alive_count_max: int = 6
    strict_host_key_checking: str = "accept-new"


@dataclass(frozen=True)
class VersionConfig:
    os_distribution: str = "oracle_linux"
    os_version: str = "8.10"
    oracle_version: str = "19c"
    oracle_home_version: str = "19.0.0"
    patch_set: str = "19.30"


@dataclass(frozen=True)
class OSConfig:
    distribution: str = "oracle_linux"
    version: str = "8.10"
    package_manager: str = "dnf"
    preinstall_package: str = "oracle-database-preinstall-19c"
    selinux_mode: str = "permissive"
    ntp_servers: list[str] = field(default_factory=lambda: list(DEFAULT_NTP_SERVERS))


@dataclass(frozen=True)
class DNSConfig:
    resolvers: list[str]
    search_domains: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NodeConfig:
    host: str
    public_ip: str
    private_ip: str | None = None
    vip_ip: str | None = None
    user: str | None = None
    role: str | None = None

    @property
    def ssh_user(self) -> str | None:
        return self.user

    @property
    def short_name(self) -> str:
        return self.host.split(".", 1)[0]

    @property
    def domain(self) -> str:
        parts = self.host.split(".", 1)
        return parts[1] if len(parts) == 2 else ""

    @property
    def private_hostname(self) -> str:
        return _derived_hostname(self.host, "priv")

    @property
    def vip_hostname(self) -> str:
        return _derived_hostname(self.host, "vip")


@dataclass(frozen=True)
class SiteConfig:
    name: str
    nodes: list[NodeConfig]
    db_unique_name: str
    db_name: str | None = None
    scan_name: str | None = None


@dataclass(frozen=True)
class ASMDiskConfig:
    uuid: str | None = None
    path: str | None = None
    name: str | None = None

    @property
    def dm_uuid(self) -> str:
        if not self.uuid:
            raise ConfigError("ASM disk does not define a DM_UUID.")
        return self.uuid if self.uuid.startswith("mpath-") else f"mpath-{self.uuid}"

    @property
    def source_path(self) -> str:
        if self.path:
            return self.path
        return self.symlink_path("asm", 1)

    def final_path(self, group: str, index: int) -> str:
        if self.path and self.path.startswith("/dev/oracleasm/"):
            return self.path
        return self.symlink_path(group, index)

    def symlink_name(self, group: str, index: int) -> str:
        return self.name or f"{group.lower()}{index:02d}"

    def symlink_path(self, group: str, index: int) -> str:
        return f"/dev/oracleasm/{self.symlink_name(group, index)}"


@dataclass(frozen=True)
class ASMConfig:
    data_disks: list[ASMDiskConfig]
    reco_disks: list[ASMDiskConfig]
    ocr_disks: list[ASMDiskConfig] = field(default_factory=list)
    redundancy: str = "EXTERNAL"

    @property
    def all_disks(self) -> list[ASMDiskConfig]:
        return [*self.ocr_disks, *self.data_disks, *self.reco_disks]

    @property
    def all_dm_uuids(self) -> list[str]:
        return [disk.dm_uuid for disk in self.all_disks if disk.uuid]


@dataclass(frozen=True)
class PatchConfig:
    file: str
    name: str | None = None
    type: str = "ru"
    description: str | None = None

    @property
    def label(self) -> str:
        return self.name or self.file


@dataclass(frozen=True)
class InstallerConfig:
    sources_path: str = "/u01/sources"
    grid_zip: str = ""
    db_zip: str = ""
    opatch_zip: str | None = None
    grid_patch: PatchConfig | None = None
    db_patch: PatchConfig | None = None
    ojvm_patch: PatchConfig | None = None

    @property
    def patches(self) -> list[PatchConfig]:
        return [patch for patch in (self.grid_patch, self.db_patch, self.ojvm_patch) if patch is not None]


@dataclass(frozen=True)
class DataGuardConfig:
    configuration_method: str = "broker"
    protection_mode: str = "max_performance"


@dataclass(frozen=True)
class SecretsConfig:
    sys_password_env: str = "ORACLE_AUTO_SYS_PASSWORD"
    system_password_env: str = "ORACLE_AUTO_SYSTEM_PASSWORD"
    asmsnmp_password_env: str = "ORACLE_AUTO_ASMSNMP_PASSWORD"
    dg_password_env: str = "ORACLE_AUTO_DG_PASSWORD"


@dataclass(frozen=True)
class AutomationConfig:
    install_type: str
    primary_site: SiteConfig
    asm: ASMConfig
    dns: DNSConfig
    installer: InstallerConfig
    standby_site: SiteConfig | None = None
    dataguard: DataGuardConfig = field(default_factory=DataGuardConfig)
    secrets: SecretsConfig = field(default_factory=SecretsConfig)
    version: VersionConfig = field(default_factory=VersionConfig)
    os: OSConfig = field(default_factory=OSConfig)
    ssh: SSHConfig = field(default_factory=SSHConfig)
    run_id: str = "default"

    @property
    def sources_path(self) -> str:
        return self.installer.sources_path

    @property
    def patch_version(self) -> str:
        return self.version.patch_set

    @property
    def active_dataguard_enabled(self) -> bool:
        return self.standby_site is not None

    @property
    def all_nodes(self) -> list[NodeConfig]:
        nodes = list(self.primary_site.nodes)
        if self.standby_site:
            nodes.extend(self.standby_site.nodes)
        return nodes

    @property
    def sites(self) -> list[SiteConfig]:
        sites = [self.primary_site]
        if self.standby_site:
            sites.append(self.standby_site)
        return sites


def load_config(path: Path) -> AutomationConfig:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    data = _load_mapping(path)
    config = _parse_config(data, path)
    _validate_config(config)
    return config


def _load_mapping(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    raw = path.read_text(encoding="utf-8")

    if suffix == ".json":
        data = json.loads(raw)
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise ConfigError("YAML config requires optional dependency: pip install PyYAML") from exc
        data = yaml.safe_load(raw)
    else:
        raise ConfigError("Unsupported config format. Use .json, .yaml, or .yml.")

    if not isinstance(data, dict):
        raise ConfigError("Config root must be an object/mapping.")
    return data


def _parse_config(data: dict[str, Any], path: Path) -> AutomationConfig:
    try:
        install_type = str(data["install_type"])
        primary_site = _parse_site("primary_site", data["primary_site"])
        asm = _parse_asm(data["asm"])
        dns = _parse_dns(data["dns"])
        installer = _parse_installer(data["installer"])
    except KeyError as exc:
        raise ConfigError(f"Missing required config key: {exc.args[0]}") from exc

    standby_site = None
    if data.get("standby_site") is not None:
        standby_site = _parse_site("standby_site", data["standby_site"])

    version = _parse_version(data.get("version", {}))
    os_config = _parse_os(data.get("os", {}), version)
    ssh = _parse_ssh(data.get("ssh", {}))
    dataguard = _parse_dataguard(data.get("dataguard", {}))
    secrets = _parse_secrets(data.get("secrets", {}))
    run_id = str(data.get("run_id") or path.stem)

    return AutomationConfig(
        install_type=install_type,
        primary_site=primary_site,
        standby_site=standby_site,
        asm=asm,
        dns=dns,
        installer=installer,
        dataguard=dataguard,
        secrets=secrets,
        version=version,
        os=os_config,
        ssh=ssh,
        run_id=run_id,
    )


def _parse_site(name: str, data: Any) -> SiteConfig:
    if not isinstance(data, dict):
        raise ConfigError(f"{name} must be an object/mapping.")

    nodes_raw = data.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise ConfigError(f"{name}.nodes must be a non-empty list.")

    nodes = [_parse_node(node, f"{name}.nodes[{index}]") for index, node in enumerate(nodes_raw)]

    db_unique_name = _optional_str(data.get("db_unique_name"))
    if not db_unique_name:
        raise ConfigError(f"{name}.db_unique_name is required.")

    return SiteConfig(
        name=str(data.get("name", name)),
        nodes=nodes,
        db_name=_optional_str(data.get("db_name")),
        db_unique_name=db_unique_name,
        scan_name=_optional_str(data.get("scan_name")),
    )


def _parse_node(data: Any, location: str) -> NodeConfig:
    if not isinstance(data, dict):
        raise ConfigError(f"{location} must be an object with host and public_ip.")
    if not data.get("host"):
        raise ConfigError(f"{location}.host is required.")
    public_ip = _optional_str(data.get("public_ip") or data.get("ip"))
    if not public_ip:
        raise ConfigError(f"{location}.public_ip is required.")
    return NodeConfig(
        host=str(data["host"]),
        public_ip=public_ip,
        private_ip=_optional_str(data.get("private_ip")),
        vip_ip=_optional_str(data.get("vip_ip")),
        user=_optional_str(data.get("user")),
        role=_optional_str(data.get("role")),
    )


def _parse_asm(data: Any) -> ASMConfig:
    if not isinstance(data, dict):
        raise ConfigError("asm must be an object/mapping.")
    return ASMConfig(
        data_disks=_required_asm_disk_list(data.get("data_disks"), "asm.data_disks"),
        reco_disks=_required_asm_disk_list(data.get("reco_disks"), "asm.reco_disks"),
        ocr_disks=_optional_asm_disk_list(data.get("ocr_disks"), "asm.ocr_disks"),
        redundancy=str(data.get("redundancy", "EXTERNAL")).upper(),
    )


def _parse_dns(data: Any) -> DNSConfig:
    if not isinstance(data, dict):
        raise ConfigError("dns must be an object/mapping.")
    return DNSConfig(
        resolvers=_required_str_list(data.get("resolvers"), "dns.resolvers"),
        search_domains=[str(item) for item in data.get("search_domains", [])],
    )


def _parse_installer(data: Any) -> InstallerConfig:
    if not isinstance(data, dict):
        raise ConfigError("installer must be an object/mapping.")
    legacy_patches = _parse_legacy_patches(data.get("patches", []))
    grid_patch = _parse_optional_patch(data.get("grid_patch"), "installer.grid_patch")
    db_patch = _parse_optional_patch(data.get("db_patch"), "installer.db_patch")
    ojvm_patch = _parse_optional_patch(data.get("ojvm_patch"), "installer.ojvm_patch")
    if legacy_patches:
        if grid_patch is None:
            grid_patch = legacy_patches[0]
        if db_patch is None:
            db_patch = legacy_patches[1] if len(legacy_patches) > 1 else legacy_patches[0]
        if ojvm_patch is None and len(legacy_patches) > 2:
            ojvm_patch = legacy_patches[2]
    return InstallerConfig(
        sources_path=str(data.get("sources_path", "/u01/sources")),
        grid_zip=str(data.get("grid_zip", "")),
        db_zip=str(data.get("db_zip", "")),
        opatch_zip=_optional_str(data.get("opatch_zip")),
        grid_patch=grid_patch,
        db_patch=db_patch,
        ojvm_patch=ojvm_patch,
    )


def _parse_legacy_patches(data: Any) -> list[PatchConfig]:
    if data is None:
        return []
    if not isinstance(data, list):
        raise ConfigError("installer.patches must be a list.")
    return [_parse_patch(item, f"installer.patches[{index}]") for index, item in enumerate(data)]


def _parse_optional_patch(data: Any, location: str) -> PatchConfig | None:
    if data is None:
        return None
    return _parse_patch(data, location)


def _parse_patch(data: Any, location: str) -> PatchConfig:
    if isinstance(data, str):
        return PatchConfig(file=data)
    if not isinstance(data, dict):
        raise ConfigError(f"{location} must be a filename string or object.")
    if not data.get("file"):
        raise ConfigError(f"{location}.file is required.")
    return PatchConfig(
        file=str(data["file"]),
        name=_optional_str(data.get("name")),
        type=str(data.get("type", "ru")),
        description=_optional_str(data.get("description")),
    )


def _parse_dataguard(data: Any) -> DataGuardConfig:
    if data is None:
        return DataGuardConfig()
    if not isinstance(data, dict):
        raise ConfigError("dataguard must be an object/mapping.")
    return DataGuardConfig(
        configuration_method=str(data.get("configuration_method", "broker")),
        protection_mode=str(data.get("protection_mode", "max_performance")),
    )


def _parse_secrets(data: Any) -> SecretsConfig:
    if data is None:
        return SecretsConfig()
    if not isinstance(data, dict):
        raise ConfigError("secrets must be an object/mapping.")
    return SecretsConfig(
        sys_password_env=str(data.get("sys_password_env", "ORACLE_AUTO_SYS_PASSWORD")),
        system_password_env=str(data.get("system_password_env", "ORACLE_AUTO_SYSTEM_PASSWORD")),
        asmsnmp_password_env=str(data.get("asmsnmp_password_env", "ORACLE_AUTO_ASMSNMP_PASSWORD")),
        dg_password_env=str(data.get("dg_password_env", "ORACLE_AUTO_DG_PASSWORD")),
    )


def _parse_version(data: Any) -> VersionConfig:
    if data is None:
        return VersionConfig()
    if not isinstance(data, dict):
        raise ConfigError("version must be an object/mapping.")
    return VersionConfig(
        os_distribution=str(data.get("os_distribution", "oracle_linux")),
        os_version=str(data.get("os_version", "8.10")),
        oracle_version=str(data.get("oracle_version", "19c")),
        oracle_home_version=str(data.get("oracle_home_version", "19.0.0")),
        patch_set=str(data.get("patch_set", "19.30")),
    )


def _parse_os(data: Any, version: VersionConfig) -> OSConfig:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("os must be an object/mapping.")
    return OSConfig(
        distribution=str(data.get("distribution", version.os_distribution)),
        version=str(data.get("version", version.os_version)),
        package_manager=str(data.get("package_manager", "dnf")),
        preinstall_package=str(data.get("preinstall_package", "oracle-database-preinstall-19c")),
        selinux_mode=str(data.get("selinux_mode", "permissive")),
        ntp_servers=[str(item) for item in data.get("ntp_servers", DEFAULT_NTP_SERVERS)],
    )


def _parse_ssh(data: Any) -> SSHConfig:
    if data is None:
        return SSHConfig()
    if not isinstance(data, dict):
        raise ConfigError("ssh must be an object/mapping.")
    return SSHConfig(
        user=str(data.get("user", "root")),
        port=int(data.get("port", 22)),
        key_file=_optional_str(data.get("key_file")),
        connect_timeout=int(data.get("connect_timeout", 10)),
        server_alive_interval=int(data.get("server_alive_interval", 30)),
        server_alive_count_max=int(data.get("server_alive_count_max", 6)),
        strict_host_key_checking=str(data.get("strict_host_key_checking", "accept-new")),
    )


def _validate_config(config: AutomationConfig) -> None:
    if config.install_type not in VALID_INSTALL_TYPES:
        raise ConfigError(f"install_type must be one of: {', '.join(sorted(VALID_INSTALL_TYPES))}")
    if not config.installer.sources_path.startswith("/"):
        raise ConfigError("installer.sources_path must be an absolute path on the target server.")
    if config.dataguard.configuration_method not in VALID_DATAGUARD_METHODS:
        raise ConfigError("dataguard.configuration_method must be manual or broker.")
    if config.dataguard.protection_mode != "max_performance":
        raise ConfigError("Only dataguard.protection_mode=max_performance is supported by default.")
    if config.os.selinux_mode.lower() != "permissive":
        raise ConfigError("SELinux baseline must be permissive.")
    if config.os.package_manager not in {"dnf", "yum"}:
        raise ConfigError("os.package_manager must be dnf or yum.")
    for env_name in (
        config.secrets.sys_password_env,
        config.secrets.system_password_env,
        config.secrets.asmsnmp_password_env,
        config.secrets.dg_password_env,
    ):
        if not env_name or not env_name.replace("_", "").isalnum() or env_name[0].isdigit():
            raise ConfigError(f"Invalid secret environment variable name: {env_name}")
    duplicate_secret_env = _duplicates(
        [
            config.secrets.sys_password_env,
            config.secrets.system_password_env,
            config.secrets.asmsnmp_password_env,
            config.secrets.dg_password_env,
        ]
    )
    if duplicate_secret_env:
        raise ConfigError(f"Duplicate secret environment variable name(s): {', '.join(duplicate_secret_env)}")
    if not config.installer.grid_zip:
        raise ConfigError("installer.grid_zip is required.")
    if not config.installer.db_zip:
        raise ConfigError("installer.db_zip is required.")

    if config.install_type == "rac":
        _validate_rac_site(config.primary_site, "primary_site")
        if config.standby_site:
            _validate_rac_site(config.standby_site, "standby_site")
    elif len(config.primary_site.nodes) != 1:
        raise ConfigError("single-gi requires exactly one primary_site node.")

    if config.standby_site:
        if len(config.standby_site.nodes) != len(config.primary_site.nodes):
            raise ConfigError("standby_site must have the same node count as primary_site.")
        if config.install_type == "single-gi" and len(config.standby_site.nodes) != 1:
            raise ConfigError("single-gi standby must also be single node.")
        if not config.standby_site.db_unique_name:
            raise ConfigError("standby_site.db_unique_name is required when standby_site is set.")

    duplicates = _duplicates([node.host for node in config.all_nodes])
    if duplicates:
        raise ConfigError(f"Duplicate host(s) in config: {', '.join(duplicates)}")

    duplicated_ips = _duplicates([node.public_ip for node in config.all_nodes])
    if duplicated_ips:
        raise ConfigError(f"Duplicate public IP(s) in config: {', '.join(duplicated_ips)}")
    _validate_ip_values(config)
    _validate_unique_addresses(config)
    _validate_unique_generated_names(config)
    _validate_scan_names(config)
    _validate_asm_disk_counts(config)

    duplicated_disks = _duplicates(config.asm.all_dm_uuids)
    if duplicated_disks:
        raise ConfigError(f"Duplicate ASM disk DM_UUID(s) in config: {', '.join(duplicated_disks)}")
    duplicated_symlinks = _duplicates(_asm_symlink_names(config))
    if duplicated_symlinks:
        raise ConfigError(f"Duplicate ASM udev symlink name(s) in config: {', '.join(duplicated_symlinks)}")


def _validate_rac_site(site: SiteConfig, label: str) -> None:
    if len(site.nodes) < 2:
        raise ConfigError(f"{label} requires at least two nodes for install_type=rac.")
    if not site.scan_name:
        raise ConfigError(f"{label}.scan_name is required for install_type=rac.")
    for index, node in enumerate(site.nodes):
        location = f"{label}.nodes[{index}]"
        if not node.private_ip:
            raise ConfigError(f"{location}.private_ip is required for RAC.")
        if not node.vip_ip:
            raise ConfigError(f"{location}.vip_ip is required for RAC.")


def _validate_unique_addresses(config: AutomationConfig) -> None:
    addresses: list[str] = []
    for node in config.all_nodes:
        addresses.append(node.public_ip)
        if node.private_ip:
            addresses.append(node.private_ip)
        if node.vip_ip:
            addresses.append(node.vip_ip)
    duplicates = _duplicates(addresses)
    if duplicates:
        raise ConfigError(f"Duplicate IP address(es) across public/private/VIP config: {', '.join(duplicates)}")


def _validate_unique_generated_names(config: AutomationConfig) -> None:
    names: list[str] = []
    for node in config.all_nodes:
        names.append(node.host)
        if node.private_ip:
            names.append(node.private_hostname)
        if node.vip_ip:
            names.append(node.vip_hostname)
    duplicates = _duplicates(names)
    if duplicates:
        raise ConfigError(f"Duplicate generated hostname(s): {', '.join(duplicates)}")


def _validate_scan_names(config: AutomationConfig) -> None:
    scans = [site.scan_name for site in config.sites if site.scan_name]
    duplicate_scans = _duplicates([scan for scan in scans if scan])
    if duplicate_scans:
        raise ConfigError(f"Duplicate SCAN name(s): {', '.join(duplicate_scans)}")

    generated_names = {node.host for node in config.all_nodes}
    for node in config.all_nodes:
        generated_names.add(node.private_hostname)
        generated_names.add(node.vip_hostname)
    conflicts = sorted(scan for scan in scans if scan in generated_names)
    if conflicts:
        raise ConfigError(f"SCAN name must not match public/private/VIP hostname(s): {', '.join(conflicts)}")


def _validate_asm_disk_counts(config: AutomationConfig) -> None:
    min_count = {"EXTERNAL": 1, "NORMAL": 2, "HIGH": 3}.get(config.asm.redundancy)
    if min_count is None:
        raise ConfigError("asm.redundancy must be EXTERNAL, NORMAL, or HIGH.")
    groups = [
        ("DATA", config.asm.data_disks),
        ("RECO", config.asm.reco_disks),
    ]
    if config.install_type == "rac":
        groups.insert(0, ("OCR", config.asm.ocr_disks))
    elif config.asm.ocr_disks:
        raise ConfigError("asm.ocr_disks is only used for install_type=rac; omit it for single-gi.")

    for group, disks in groups:
        if len(disks) < min_count:
            raise ConfigError(f"asm.{group.lower()}_disks requires at least {min_count} disk(s) for {config.asm.redundancy} redundancy.")


def _required_str_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{name} must be a non-empty list.")
    items = [str(item) for item in value]
    if any(not item for item in items):
        raise ConfigError(f"{name} cannot contain empty values.")
    return items


def _required_asm_disk_list(value: Any, name: str) -> list[ASMDiskConfig]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{name} must be a non-empty list.")
    return _parse_asm_disk_list(value, name)


def _optional_asm_disk_list(value: Any, name: str) -> list[ASMDiskConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a list.")
    if not value:
        return []
    return _parse_asm_disk_list(value, name)


def _parse_asm_disk_list(value: list[Any], name: str) -> list[ASMDiskConfig]:
    disks: list[ASMDiskConfig] = []
    for index, item in enumerate(value):
        location = f"{name}[{index}]"
        if isinstance(item, str):
            uuid = item
            path = None
            disk_name = None
        elif isinstance(item, dict):
            uuid = _optional_str(item.get("uuid"))
            path = _optional_str(item.get("path"))
            disk_name = _optional_str(item.get("name"))
        else:
            raise ConfigError(f"{location} must be a DM_UUID string or object.")

        if not uuid and not path:
            raise ConfigError(f"{location}.uuid or {location}.path is required.")
        if uuid and uuid.startswith("/dev/"):
            raise ConfigError(f"{location}.uuid must contain DM_UUID only, not a device path.")
        if path and not path.startswith("/dev/"):
            raise ConfigError(f"{location}.path must be an absolute /dev path.")
        if disk_name and ("/" in disk_name or disk_name.startswith(".")):
            raise ConfigError(f"{location}.name must be a simple symlink name.")

        disks.append(ASMDiskConfig(uuid=uuid, path=path, name=disk_name))
    return disks


def _validate_ip_values(config: AutomationConfig) -> None:
    for node in config.all_nodes:
        _validate_ip(node.public_ip, f"{node.host}.public_ip")
        if node.private_ip:
            _validate_ip(node.private_ip, f"{node.host}.private_ip")
        if node.vip_ip:
            _validate_ip(node.vip_ip, f"{node.host}.vip_ip")
    for resolver in config.dns.resolvers:
        _validate_ip(resolver, "dns.resolvers")
    for server in config.os.ntp_servers:
        _validate_ip(server, "os.ntp_servers")


def _validate_ip(value: str, label: str) -> None:
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise ConfigError(f"{label} must be a valid IP address: {value}") from exc


def _asm_symlink_names(config: AutomationConfig) -> list[str]:
    names: list[str] = []
    for group, disks in (
        ("OCR", config.asm.ocr_disks),
        ("DATA", config.asm.data_disks),
        ("RECO", config.asm.reco_disks),
    ):
        names.extend(disk.symlink_name(group, index) for index, disk in enumerate(disks, start=1))
    return names


def _duplicates(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _derived_hostname(host: str, suffix: str) -> str:
    short, separator, domain = host.partition(".")
    derived = f"{short}-{suffix}"
    return f"{derived}.{domain}" if separator else derived
