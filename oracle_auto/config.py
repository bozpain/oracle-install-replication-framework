"""Configuration schema and validation manual.

This module is the contract for every automation phase. Keep all deployment
decisions here first, then let runners consume typed dataclasses instead of raw
JSON/YAML. The intended operator workflow is:

1. Fill one deployment config for `single-gi` or `rac`.
2. Provide public IPs, private IPs, RAC VIP IPs, SCAN DNS names, ASM disk paths/DM_UUIDs, and installer ZIPs.
3. Let the framework derive `-priv` and `-vip` hostnames, validate topology, and
   drive all later commands from this normalized model.

SCAN names may be supplied by DNS or by configured `scan_ip`/`scan_ips` values
that are written to `/etc/hosts` during OS preparation. Public, private, VIP,
and configured SCAN host file entries are validated locally instead of requiring
external DNS records.
"""

from __future__ import annotations

import json
import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


VALID_INSTALL_TYPES = {"single-gi", "rac"}
VALID_DATAGUARD_METHODS = {"manual", "broker"}
VALID_ASM_STORAGE_MODES = {"asmlibv3", "raw", "afd"}
DEFAULT_STANDBY_REDO_LOG_SIZE = "200M"
DEFAULT_PUBLIC_INTERFACE = "eth0"
DEFAULT_PRIVATE_INTERFACE = "eth1"
DEFAULT_PUBLIC_NETWORK_PREFIX = 24
DEFAULT_PRIVATE_NETWORK_PREFIX = 24
DEFAULT_NTP_SERVERS = ["192.168.113.41", "192.168.115.41"]
DEFAULT_ASMLIB_RPMS = {
    "x86_64": "oracleasmlib-3.1.1-1.el8.x86_64.rpm",
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SSHConfig:
    user: str = "root"
    port: int = 22
    key_file: str | None = None
    connect_timeout: int | None = None
    server_alive_interval: int | None = None
    server_alive_count_max: int | None = None
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
    asmlib_rpms: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ASMLIB_RPMS))


@dataclass(frozen=True)
class DNSConfig:
    resolvers: list[str]
    search_domains: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NodeConfig:
    host: str
    public_ip: str
    ssh_host: str | None = None
    private_ip: str | None = None
    vip_ip: str | None = None
    public_subnet_anchor_ip: str | None = None
    private_subnet_anchor_ip: str | None = None
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
    scan_ips: list[str] = field(default_factory=list)
    network_interface_list: str | None = None


@dataclass(frozen=True)
class ASMDiskConfig:
    uuid: str | None = None
    id_serial: str | None = None
    id_wwn: str | None = None
    path: str | None = None
    site_paths: dict[str, str] = field(default_factory=dict)
    node_paths: dict[str, str] = field(default_factory=dict)
    name: str | None = None

    @property
    def dm_uuid(self) -> str:
        if not self.uuid:
            raise ConfigError("ASM disk does not define a DM_UUID.")
        return self.uuid if self.uuid.startswith("mpath-") else f"mpath-{self.uuid}"

    @property
    def source_path(self) -> str:
        return self.path_for()

    def path_for(self, site_name: str | None = None, node_host: str | None = None) -> str:
        if node_host and node_host in self.node_paths:
            return self.node_paths[node_host]
        if site_name and site_name in self.site_paths:
            return self.site_paths[site_name]
        if self.path:
            return self.path
        if self.uuid:
            return f"/dev/disk/by-id/dm-uuid-{self.dm_uuid}"
        if self.id_wwn:
            return f"/dev/disk/by-id/{self.id_wwn if self.id_wwn.startswith('wwn-') else 'wwn-' + self.id_wwn}"
        if self.id_serial:
            return f"/dev/disk/by-id/{self.id_serial}"
        if self.site_paths:
            return next(iter(self.site_paths.values()))
        if self.node_paths:
            return next(iter(self.node_paths.values()))
        raise ConfigError("ASM disk does not define a path, site_paths, node_paths, DM_UUID, ID_SERIAL, or ID_WWN.")

    def source_for(self, site_name: str | None = None, node_host: str | None = None) -> str:
        if node_host and node_host in self.node_paths:
            return self.node_paths[node_host]
        if site_name and site_name in self.site_paths:
            return self.site_paths[site_name]
        if self.path:
            return self.path
        if self.uuid:
            return f"DM_UUID={self.dm_uuid}"
        if self.id_serial:
            return f"ID_SERIAL={self.id_serial}"
        if self.id_wwn:
            return f"ID_WWN={self.id_wwn}"
        return self.path_for(site_name=site_name, node_host=node_host)

    def final_path(self, group: str, index: int, site_name: str | None = None, node_host: str | None = None) -> str:
        return self.path_for(site_name=site_name, node_host=node_host)

    def symlink_name(self, group: str, index: int) -> str:
        return self.name or f"{group.lower()}{index:02d}"

    def symlink_path(self, group: str, index: int) -> str:
        return self.final_path(group, index)


@dataclass(frozen=True)
class ASMConfig:
    data_disks: list[ASMDiskConfig]
    reco_disks: list[ASMDiskConfig]
    ocr_disks: list[ASMDiskConfig] = field(default_factory=list)
    redundancy: str = "EXTERNAL"
    storage_mode: str = "asmlibv3"

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
    patch_id: str | None = None
    type: str = "ru"
    description: str | None = None

    @property
    def label(self) -> str:
        return self.name or self.file


@dataclass(frozen=True)
class PatchManifest:
    patch_id: str
    description: str
    grid_zip: str | None
    db_zip: str | None
    opatch_zip: str
    gi_zip: str
    dbru_zip: str
    ojvm_zip: str
    opatch_dir: str
    gi_dir: str
    dbru_dir: str
    ojvm_dir: str
    oracleasmlib_rpm_x86_64: str | None = None
    pre_datapatch_sql: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class InstallerConfig:
    sources_path: str = "/u01/sources"
    grid_zip: str = ""
    db_zip: str = ""
    opatch_zip: str | None = None
    grid_patch: PatchConfig | None = None
    db_patch: PatchConfig | None = None
    ojvm_patch: PatchConfig | None = None
    patch_manifest: PatchManifest | None = None

    @property
    def patches(self) -> list[PatchConfig]:
        return [patch for patch in (self.grid_patch, self.db_patch, self.ojvm_patch) if patch is not None]


@dataclass(frozen=True)
class DataGuardConfig:
    configuration_method: str | None = None
    protection_mode: str = "max_performance"
    standby_redo_log_size: str = DEFAULT_STANDBY_REDO_LOG_SIZE


@dataclass(frozen=True)
class SecretsConfig:
    sys_password_env: str = "ORACLE_AUTO_SYS_PASSWORD"
    system_password_env: str = "ORACLE_AUTO_SYSTEM_PASSWORD"
    asmsnmp_password_env: str = "ORACLE_AUTO_ASMSNMP_PASSWORD"
    dg_password_env: str = "ORACLE_AUTO_DG_PASSWORD"


@dataclass(frozen=True)
class ReportPublishConfig:
    path: str | None = None
    url_base: str | None = None


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
    report_publish: ReportPublishConfig = field(default_factory=ReportPublishConfig)
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

    def site_for_node(self, node: NodeConfig) -> SiteConfig:
        for site in self.sites:
            if any(item.host == node.host for item in site.nodes):
                return site
        raise ConfigError(f"Node is not part of any configured site: {node.host}")


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
        dns = _parse_dns(data.get("dns", {}))
        version = _parse_version(data.get("version", {}), data)
        installer = _parse_installer(data.get("installer", {}), version, path)
    except KeyError as exc:
        raise ConfigError(f"Missing required config key: {exc.args[0]}") from exc

    standby_site = None
    if data.get("standby_site") is not None:
        standby_site = _parse_site("standby_site", data["standby_site"])

    os_config = _parse_os(data.get("os", {}), version, installer.patch_manifest)
    ssh = _parse_ssh(data.get("ssh", {}))
    dataguard = _parse_dataguard(data.get("dataguard", {}))
    secrets = _parse_secrets(data.get("secrets", {}))
    report_publish = _parse_report_publish(data.get("report_publish", {}))
    run_id = str(data.get("run_id") or data.get("app_name") or path.stem)

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
        report_publish=report_publish,
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
        scan_ips=_parse_scan_ips(data),
        network_interface_list=_optional_str(data.get("network_interface_list")),
    )


def _parse_scan_ips(data: dict[str, Any]) -> list[str]:
    if data.get("scan_ips") is not None:
        raw = data["scan_ips"]
        if not isinstance(raw, list):
            raise ConfigError("scan_ips must be a list.")
        values = [str(item) for item in raw]
    elif data.get("scan_ip") is not None:
        values = [str(data["scan_ip"])]
    else:
        values = []
    if any(not value for value in values):
        raise ConfigError("scan_ips cannot contain empty values.")
    return values


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
        ssh_host=_optional_str(data.get("ssh_host")),
        private_ip=_optional_str(data.get("private_ip")),
        vip_ip=_optional_str(data.get("vip_ip")),
        public_subnet_anchor_ip=_optional_str(data.get("public_subnet_anchor_ip")),
        private_subnet_anchor_ip=_optional_str(data.get("private_subnet_anchor_ip")),
        user=_optional_str(data.get("user")),
        role=_optional_str(data.get("role")),
    )


def _parse_asm(data: Any) -> ASMConfig:
    if not isinstance(data, dict):
        raise ConfigError("asm must be an object/mapping.")
    storage_mode = _normalize_asm_storage_mode(data.get("storage_mode", "asmlibv3"))
    site_disks = data.get("sites")
    if site_disks is not None:
        legacy_keys = {"data_disks", "reco_disks", "ocr_disks"} & set(data)
        if legacy_keys:
            keys = ", ".join(sorted(legacy_keys))
            raise ConfigError(f"asm.sites cannot be combined with legacy ASM disk key(s): {keys}.")
        disk_groups = _parse_asm_site_layout(site_disks)
        data_disks = disk_groups["data"]
        reco_disks = disk_groups["reco"]
        ocr_disks = disk_groups["ocr"]
    else:
        data_disks = _required_asm_disk_list(data.get("data_disks"), "asm.data_disks")
        reco_disks = _required_asm_disk_list(data.get("reco_disks"), "asm.reco_disks")
        ocr_disks = _optional_asm_disk_list(data.get("ocr_disks"), "asm.ocr_disks")
    return ASMConfig(
        data_disks=data_disks,
        reco_disks=reco_disks,
        ocr_disks=ocr_disks,
        redundancy=str(data.get("redundancy", "EXTERNAL")).upper(),
        storage_mode=storage_mode,
    )


def _normalize_asm_storage_mode(value: Any) -> str:
    storage_mode = str(value).lower().replace("-", "_")
    if storage_mode == "asmlib":
        return "asmlibv3"
    if storage_mode == "raw_udev":
        return "raw"
    return storage_mode


def _parse_dns(data: Any) -> DNSConfig:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("dns must be an object/mapping.")
    return DNSConfig(
        resolvers=[str(item) for item in data.get("resolvers", DEFAULT_NTP_SERVERS)],
        search_domains=[str(item) for item in data.get("search_domains", [])],
    )


def _parse_installer(data: Any, version: VersionConfig, config_path: Path) -> InstallerConfig:
    if not isinstance(data, dict):
        raise ConfigError("installer must be an object/mapping.")

    manifest = _parse_patch_manifest(data, version, config_path)
    if manifest is not None:
        grid_patch = PatchConfig(
            file=manifest.gi_zip,
            name=f"{manifest.patch_id} Grid RU",
            patch_id=manifest.gi_dir,
            type="ru",
            description=manifest.description,
        )
        db_patch = PatchConfig(
            file=manifest.dbru_zip,
            name=f"{manifest.patch_id} Database RU",
            patch_id=manifest.dbru_dir,
            type="ru",
            description=manifest.description,
        )
        ojvm_patch = PatchConfig(
            file=manifest.ojvm_zip,
            name=f"{manifest.patch_id} OJVM RU",
            patch_id=manifest.ojvm_dir,
            type="ojvm",
            description=manifest.description,
        )
        opatch_zip = manifest.opatch_zip
    else:
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
        opatch_zip = _optional_str(data.get("opatch_zip"))

    return InstallerConfig(
        sources_path=str(data.get("sources_path", "/u01/sources")),
        grid_zip=str(data.get("grid_zip") or (manifest.grid_zip if manifest else "") or ""),
        db_zip=str(data.get("db_zip") or (manifest.db_zip if manifest else "") or ""),
        opatch_zip=opatch_zip,
        grid_patch=grid_patch,
        db_patch=db_patch,
        ojvm_patch=ojvm_patch,
        patch_manifest=manifest,
    )


def _parse_patch_manifest(data: dict[str, Any], version: VersionConfig, config_path: Path) -> PatchManifest | None:
    manifest_selector = _optional_str(data.get("patch_manifest") or data.get("manifest"))
    legacy_patch_keys = {"opatch_zip", "patches", "grid_patch", "db_patch", "ojvm_patch"}
    if manifest_selector is None and not any(key in data for key in legacy_patch_keys):
        manifest_selector = version.patch_set
    if manifest_selector is None:
        return None

    manifest_path = _resolve_manifest_path(manifest_selector, config_path)
    if not manifest_path.exists():
        raise ConfigError(f"Patch manifest not found: {manifest_path}")
    raw = _load_manifest_mapping(manifest_path)
    manifest = _manifest_from_mapping(raw, manifest_path)
    if manifest.patch_id != version.patch_set:
        raise ConfigError(
            f"Patch manifest patch_id mismatch: version.patch_set={version.patch_set}, manifest.patch_id={manifest.patch_id}"
        )
    return manifest


def _resolve_manifest_path(selector: str, config_path: Path) -> Path:
    path = Path(selector)
    if path.is_absolute():
        return path
    if path.suffix.lower() in {".yaml", ".yml"} or "/" in selector or "\\" in selector:
        return (config_path.parent / path).resolve()
    framework_root = Path(__file__).resolve().parents[1]
    return framework_root / "manifests" / f"{selector}.yaml"


def _load_manifest_mapping(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        return _load_simple_yaml_mapping(raw, path)
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ConfigError(f"Patch manifest root must be an object/mapping: {path}")
    return data


def _load_simple_yaml_mapping(raw: str, path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for line_no, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise ConfigError(f"Unsupported manifest YAML syntax at {path}:{line_no}")
        key, value = stripped.split(":", 1)
        value = value.split(" #", 1)[0].strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        data[key.strip()] = value
    return data


def _manifest_from_mapping(data: dict[str, Any], path: Path) -> PatchManifest:
    required = [
        "patch_id",
        "description",
        "opatch_zip",
        "gi_zip",
        "dbru_zip",
        "ojvm_zip",
        "opatch_dir",
        "gi_dir",
        "dbru_dir",
        "ojvm_dir",
    ]
    missing = [key for key in required if not _optional_str(data.get(key))]
    if missing:
        raise ConfigError(f"Patch manifest {path} missing required key(s): {', '.join(missing)}")
    optional = ["grid_zip", "db_zip", "oracleasmlib_rpm_x86_64", "pre_datapatch_sql"]
    for key in [*required, *optional]:
        value = _optional_str(data.get(key))
        if value:
            _validate_safe_manifest_value(key, value, path)
    return PatchManifest(
        patch_id=str(data["patch_id"]),
        description=str(data["description"]),
        grid_zip=_optional_str(data.get("grid_zip")),
        db_zip=_optional_str(data.get("db_zip")),
        opatch_zip=str(data["opatch_zip"]),
        gi_zip=str(data["gi_zip"]),
        dbru_zip=str(data["dbru_zip"]),
        ojvm_zip=str(data["ojvm_zip"]),
        opatch_dir=str(data["opatch_dir"]),
        gi_dir=str(data["gi_dir"]),
        dbru_dir=str(data["dbru_dir"]),
        ojvm_dir=str(data["ojvm_dir"]),
        oracleasmlib_rpm_x86_64=_optional_str(data.get("oracleasmlib_rpm_x86_64")),
        pre_datapatch_sql=_optional_str(data.get("pre_datapatch_sql")),
        source=str(path),
    )


def _validate_safe_manifest_value(key: str, value: str, path: Path) -> None:
    try:
        _validate_safe_relative_value(key, value)
    except ConfigError as exc:
        raise ConfigError(f"Patch manifest {path} has unsafe {key}: {value}")


def _validate_safe_relative_value(label: str, value: str) -> None:
    if value in {".", ".."} or "/" in value or "\\" in value or ".." in value:
        raise ConfigError(f"{label} must be a safe relative file or directory name: {value}")


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
        patch_id=_optional_str(data.get("patch_id")),
        type=str(data.get("type", "ru")),
        description=_optional_str(data.get("description")),
    )


def _parse_dataguard(data: Any) -> DataGuardConfig:
    if data is None:
        return DataGuardConfig()
    if not isinstance(data, dict):
        raise ConfigError("dataguard must be an object/mapping.")
    if "protection_mode" in data:
        raise ConfigError("dataguard.protection_mode is fixed by the framework and must not be set in config.")
    configuration_method = _optional_str(data.get("configuration_method") or data.get("mode"))
    return DataGuardConfig(
        configuration_method=configuration_method,
        protection_mode="max_performance",
        standby_redo_log_size=str(data.get("standby_redo_log_size", DEFAULT_STANDBY_REDO_LOG_SIZE)).upper(),
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


def _parse_report_publish(data: Any) -> ReportPublishConfig:
    if data is None:
        return ReportPublishConfig()
    if not isinstance(data, dict):
        raise ConfigError("report_publish must be an object/mapping.")
    return ReportPublishConfig(
        path=_optional_str(data.get("path")),
        url_base=_optional_str(data.get("url_base")),
    )


def _parse_version(data: Any, root: dict[str, Any] | None = None) -> VersionConfig:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("version must be an object/mapping.")
    root = root or {}
    return VersionConfig(
        os_distribution=str(data.get("os_distribution", "oracle_linux")),
        os_version=str(data.get("os_version", "8.10")),
        oracle_version=str(data.get("oracle_version", "19c")),
        oracle_home_version=str(data.get("oracle_home_version", "19.0.0")),
        patch_set=str(data.get("patch_set", root.get("patch_set", "19.30"))),
    )


def _parse_os(data: Any, version: VersionConfig, manifest: PatchManifest | None = None) -> OSConfig:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("os must be an object/mapping.")
    asmlib_rpms = dict(DEFAULT_ASMLIB_RPMS)
    if manifest is not None:
        if manifest.oracleasmlib_rpm_x86_64:
            asmlib_rpms["x86_64"] = manifest.oracleasmlib_rpm_x86_64
    if data.get("asmlib_rpms") is not None:
        if not isinstance(data["asmlib_rpms"], dict):
            raise ConfigError("os.asmlib_rpms must be an object/mapping.")
        for arch, rpm in data["asmlib_rpms"].items():
            value = str(rpm)
            _validate_safe_relative_value(f"os.asmlib_rpms.{arch}", value)
            asmlib_rpms[str(arch)] = value
    return OSConfig(
        distribution=str(data.get("distribution", version.os_distribution)),
        version=str(data.get("version", version.os_version)),
        package_manager=str(data.get("package_manager", "dnf")),
        preinstall_package=str(data.get("preinstall_package", "oracle-database-preinstall-19c")),
        selinux_mode=str(data.get("selinux_mode", "permissive")),
        ntp_servers=[str(item) for item in data.get("ntp_servers", DEFAULT_NTP_SERVERS)],
        asmlib_rpms=asmlib_rpms,
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
        connect_timeout=_optional_int(data.get("connect_timeout"), "ssh.connect_timeout"),
        server_alive_interval=_optional_int(data.get("server_alive_interval"), "ssh.server_alive_interval"),
        server_alive_count_max=_optional_int(data.get("server_alive_count_max"), "ssh.server_alive_count_max"),
        strict_host_key_checking=str(data.get("strict_host_key_checking", "accept-new")),
    )


def _validate_config(config: AutomationConfig) -> None:
    if config.install_type not in VALID_INSTALL_TYPES:
        raise ConfigError(f"install_type must be one of: {', '.join(sorted(VALID_INSTALL_TYPES))}")
    if not config.installer.sources_path.startswith("/"):
        raise ConfigError("installer.sources_path must be an absolute path on the target server.")
    if config.dataguard.configuration_method is not None and config.dataguard.configuration_method not in VALID_DATAGUARD_METHODS:
        raise ConfigError("dataguard.configuration_method must be manual or broker.")
    if config.dataguard.protection_mode != "max_performance":
        raise ConfigError("Only dataguard.protection_mode=max_performance is supported by default.")
    if not re.fullmatch(r"[1-9][0-9]*[KMGTP]", config.dataguard.standby_redo_log_size):
        raise ConfigError("dataguard.standby_redo_log_size must use an Oracle size such as 200M, 512M, or 1G.")
    if config.report_publish.url_base and not config.report_publish.path:
        raise ConfigError("report_publish.path is required when report_publish.url_base is set.")
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
    for label, value in _installer_relative_values(config):
        _validate_safe_relative_value(label, value)

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
    _validate_network_interface_lists(config)
    _validate_asm_disk_counts(config)
    _validate_asm_disk_path_overrides(config)

    duplicated_disks = _duplicates(config.asm.all_dm_uuids)
    if duplicated_disks:
        raise ConfigError(f"Duplicate ASM disk DM_UUID(s) in config: {', '.join(duplicated_disks)}")
    duplicated_labels = _duplicates(_asm_label_names(config))
    if duplicated_labels:
        raise ConfigError(f"Duplicate ASM ASMLIB label name(s) in config: {', '.join(duplicated_labels)}")


def _installer_relative_values(config: AutomationConfig) -> list[tuple[str, str]]:
    values = [
        ("installer.grid_zip", config.installer.grid_zip),
        ("installer.db_zip", config.installer.db_zip),
    ]
    if config.installer.opatch_zip:
        values.append(("installer.opatch_zip", config.installer.opatch_zip))
    for patch in config.installer.patches:
        values.append((f"installer.{patch.type}_patch.file", patch.file))
        if patch.patch_id:
            values.append((f"installer.{patch.type}_patch.patch_id", patch.patch_id))
    return values


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
    for site in config.sites:
        addresses.extend(site.scan_ips)
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


def _validate_network_interface_lists(config: AutomationConfig) -> None:
    for site in config.sites:
        if not site.network_interface_list:
            continue
        entries = site.network_interface_list.split(",")
        for entry in entries:
            parts = entry.split(":")
            if len(parts) != 3:
                raise ConfigError(
                    f"{site.name}.network_interface_list entries must use interface:subnet:type format: {entry}"
                )
            interface_name, subnet, interface_type = parts
            if not interface_name:
                raise ConfigError(f"{site.name}.network_interface_list contains an empty interface name.")
            _validate_network_interface_subnet(subnet, f"{site.name}.network_interface_list subnet")
            if interface_type not in {"1", "2", "3", "4", "5"}:
                raise ConfigError(
                    f"{site.name}.network_interface_list interface type must be one of 1, 2, 3, 4, or 5: {entry}"
                )


def _validate_asm_disk_counts(config: AutomationConfig) -> None:
    if config.asm.storage_mode not in VALID_ASM_STORAGE_MODES:
        raise ConfigError(f"asm.storage_mode must be one of: {', '.join(sorted(VALID_ASM_STORAGE_MODES))}.")
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


def _validate_asm_disk_path_overrides(config: AutomationConfig) -> None:
    site_names = {site.name for site in config.sites}
    node_hosts = {node.host for node in config.all_nodes}
    for disk in config.asm.all_disks:
        unknown_sites = sorted(set(disk.site_paths) - site_names)
        if unknown_sites:
            raise ConfigError(f"ASM disk {disk.name or disk.source_path} has unknown site_paths key(s): {', '.join(unknown_sites)}")
        unknown_nodes = sorted(set(disk.node_paths) - node_hosts)
        if unknown_nodes:
            raise ConfigError(f"ASM disk {disk.name or disk.source_path} has unknown node_paths key(s): {', '.join(unknown_nodes)}")
        for site in config.sites:
            for node in site.nodes:
                disk.path_for(site_name=site.name, node_host=node.host)


def _required_str_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{name} must be a non-empty list.")
    items = [str(item) for item in value]
    if any(not item for item in items):
        raise ConfigError(f"{name} cannot contain empty values.")
    return items


def _parse_asm_site_layout(value: Any) -> dict[str, list[ASMDiskConfig]]:
    if not isinstance(value, dict) or not value:
        raise ConfigError("asm.sites must be a non-empty object/mapping.")

    paths_by_group: dict[str, dict[str, dict[str, str]]] = {"data": {}, "reco": {}, "ocr": {}}
    order_by_group: dict[str, list[str]] = {"data": [], "reco": [], "ocr": []}
    sites_by_group: dict[str, set[str]] = {"data": set(), "reco": set(), "ocr": set()}
    aliases = {
        "data": "data",
        "data_disks": "data",
        "reco": "reco",
        "reco_disks": "reco",
        "ocr": "ocr",
        "ocr_disks": "ocr",
    }
    default_prefix = {"data": "DATA", "reco": "RECO", "ocr": "OCR"}

    for raw_site, site_data in value.items():
        site = str(raw_site)
        if not site:
            raise ConfigError("asm.sites cannot contain an empty site name.")
        if not isinstance(site_data, dict):
            raise ConfigError(f"asm.sites.{site} must be an object/mapping.")

        for raw_group, group in aliases.items():
            if raw_group not in site_data:
                continue
            disks = site_data[raw_group]
            if disks is None:
                continue
            if not isinstance(disks, list) or not disks:
                raise ConfigError(f"asm.sites.{site}.{raw_group} must be a non-empty list.")
            sites_by_group[group].add(site)
            seen_names: set[str] = set()
            for index, item in enumerate(disks, start=1):
                name, path = _parse_asm_site_disk(
                    item,
                    f"asm.sites.{site}.{raw_group}[{index - 1}]",
                    default_prefix[group],
                    index,
                )
                if name in seen_names:
                    raise ConfigError(f"asm.sites.{site}.{raw_group} contains duplicate disk name: {name}")
                seen_names.add(name)
                if name not in paths_by_group[group]:
                    paths_by_group[group][name] = {}
                    order_by_group[group].append(name)
                paths_by_group[group][name][site] = path

    parsed: dict[str, list[ASMDiskConfig]] = {}
    for group in ("data", "reco", "ocr"):
        site_names = sites_by_group[group]
        items: list[dict[str, Any]] = []
        for name in order_by_group[group]:
            site_paths = paths_by_group[group][name]
            missing_sites = sorted(site_names - set(site_paths))
            if missing_sites:
                raise ConfigError(
                    f"asm.sites.{group} disk {name} is missing path for site(s): {', '.join(missing_sites)}"
                )
            items.append({"name": name, "site_paths": site_paths})
        parsed[group] = _parse_asm_disk_list(items, f"asm.sites.{group}") if items else []

    if not parsed["data"]:
        raise ConfigError("asm.sites must define data disks.")
    if not parsed["reco"]:
        raise ConfigError("asm.sites must define reco disks.")
    return parsed


def _parse_asm_site_disk(item: Any, location: str, prefix: str, index: int) -> tuple[str, str]:
    if isinstance(item, str):
        name = f"{prefix}{index:02d}"
        path = item
    elif isinstance(item, dict):
        name = _optional_str(item.get("name")) or f"{prefix}{index:02d}"
        path = _optional_str(item.get("path"))
    else:
        raise ConfigError(f"{location} must be a /dev path string or object.")
    if not name or "/" in name or name.startswith("."):
        raise ConfigError(f"{location}.name must be a simple symlink name.")
    if not path or not path.startswith("/dev/"):
        raise ConfigError(f"{location}.path must be an absolute /dev path.")
    return name, path


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
            id_serial = None
            id_wwn = None
            path = None
            disk_name = None
        elif isinstance(item, dict):
            uuid = _optional_str(item.get("uuid"))
            id_serial = _optional_str(item.get("id_serial") or item.get("ID_SERIAL"))
            id_wwn = _optional_str(item.get("id_wwn") or item.get("ID_WWN") or item.get("wwn"))
            path = _optional_str(item.get("path"))
            site_paths = _optional_path_mapping(item.get("site_paths"), f"{location}.site_paths")
            node_paths = _optional_path_mapping(item.get("node_paths"), f"{location}.node_paths")
            disk_name = _optional_str(item.get("name"))
        else:
            raise ConfigError(f"{location} must be a DM_UUID string or object.")

        if isinstance(item, str):
            site_paths = {}
            node_paths = {}

        if not uuid and not id_serial and not id_wwn and not path and not site_paths and not node_paths:
            raise ConfigError(f"{location}.uuid, {location}.id_serial, {location}.id_wwn, {location}.path, {location}.site_paths, or {location}.node_paths is required.")
        if uuid and uuid.startswith("/dev/"):
            raise ConfigError(f"{location}.uuid must contain DM_UUID only, not a device path.")
        if id_serial and "/" in id_serial:
            raise ConfigError(f"{location}.id_serial must contain ID_SERIAL only, not a device path.")
        if id_wwn and "/" in id_wwn:
            raise ConfigError(f"{location}.id_wwn must contain ID_WWN only, not a device path.")
        if path and not path.startswith("/dev/"):
            raise ConfigError(f"{location}.path must be an absolute /dev path.")
        if disk_name and ("/" in disk_name or disk_name.startswith(".")):
            raise ConfigError(f"{location}.name must be a simple symlink name.")

        disks.append(
            ASMDiskConfig(
                uuid=uuid,
                id_serial=id_serial,
                id_wwn=id_wwn,
                path=path,
                site_paths=site_paths,
                node_paths=node_paths,
                name=disk_name,
            )
        )
    return disks


def _optional_path_mapping(value: Any, name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be an object/mapping.")
    paths: dict[str, str] = {}
    for key, raw_path in value.items():
        label = str(key)
        path = _optional_str(raw_path)
        if not label:
            raise ConfigError(f"{name} cannot contain an empty key.")
        if not path or not path.startswith("/dev/"):
            raise ConfigError(f"{name}.{label} must be an absolute /dev path.")
        paths[label] = path
    return paths


def _validate_ip_values(config: AutomationConfig) -> None:
    for node in config.all_nodes:
        _validate_ip(node.public_ip, f"{node.host}.public_ip")
        if node.private_ip:
            _validate_ip(node.private_ip, f"{node.host}.private_ip")
        if node.vip_ip:
            _validate_ip(node.vip_ip, f"{node.host}.vip_ip")
        if node.public_subnet_anchor_ip:
            _validate_ip(node.public_subnet_anchor_ip, f"{node.host}.public_subnet_anchor_ip")
        if node.private_subnet_anchor_ip:
            _validate_ip(node.private_subnet_anchor_ip, f"{node.host}.private_subnet_anchor_ip")
    for site in config.sites:
        for scan_ip in site.scan_ips:
            _validate_ip(scan_ip, f"{site.name}.scan_ips")
    for resolver in config.dns.resolvers:
        _validate_ip(resolver, "dns.resolvers")
    for server in config.os.ntp_servers:
        _validate_ip(server, "os.ntp_servers")


def _validate_ip(value: str, label: str) -> None:
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise ConfigError(f"{label} must be a valid IP address: {value}") from exc


def _validate_network_interface_subnet(value: str, label: str) -> None:
    try:
        if "/" in value:
            ipaddress.ip_network(value, strict=False)
        else:
            ipaddress.ip_address(value)
    except ValueError as exc:
        raise ConfigError(f"{label} must be a valid IP address or CIDR: {value}") from exc


def network_interface_list_for_site(site: SiteConfig) -> str:
    if site.network_interface_list:
        return site.network_interface_list
    node = site.nodes[0]
    entries = [f"{DEFAULT_PUBLIC_INTERFACE}:{_network_cidr(node.public_ip, DEFAULT_PUBLIC_NETWORK_PREFIX)}:1"]
    if node.private_ip:
        entries.append(f"{DEFAULT_PRIVATE_INTERFACE}:{_network_cidr(node.private_ip, DEFAULT_PRIVATE_NETWORK_PREFIX)}:5")
    return ",".join(entries)


def _network_cidr(address: str, prefix_length: int) -> str:
    return str(ipaddress.ip_network(f"{address}/{prefix_length}", strict=False))


def _asm_label_names(config: AutomationConfig) -> list[str]:
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


def _optional_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer.") from exc


def _derived_hostname(host: str, suffix: str) -> str:
    short, separator, domain = host.partition(".")
    derived = f"{short}-{suffix}"
    return f"{derived}.{domain}" if separator else derived
