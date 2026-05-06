from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


VALID_INSTALL_TYPES = {"single-db", "single-gi", "rac-gi", "rac-db"}
RAC_INSTALL_TYPES = {"rac-gi", "rac-db"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SSHConfig:
    user: str = "root"
    port: int = 22
    key_file: str | None = None
    connect_timeout: int = 10
    strict_host_key_checking: str = "accept-new"


@dataclass(frozen=True)
class NodeConfig:
    host: str
    user: str | None = None
    role: str | None = None

    @property
    def ssh_user(self) -> str | None:
        return self.user


@dataclass(frozen=True)
class SiteConfig:
    name: str
    nodes: list[NodeConfig]
    db_name: str | None = None
    db_unique_name: str | None = None
    scan_name: str | None = None
    vip_names: list[str] = field(default_factory=list)
    private_interconnects: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReplicationConfig:
    enabled: bool = False
    mode: str = "dataguard"
    protection_mode: str = "max_performance"


@dataclass(frozen=True)
class OSConfig:
    distribution: str = "oracle_linux"
    version: str = "8.10"
    package_manager: str = "dnf"
    preinstall_package: str = "oracle-database-preinstall-19c"


@dataclass(frozen=True)
class AutomationConfig:
    install_type: str
    sources_path: str
    patch_version: str
    primary_site: SiteConfig
    standby_site: SiteConfig | None = None
    replication: ReplicationConfig = field(default_factory=ReplicationConfig)
    os: OSConfig = field(default_factory=OSConfig)
    ssh: SSHConfig = field(default_factory=SSHConfig)
    run_id: str = "default"

    @property
    def all_nodes(self) -> list[NodeConfig]:
        nodes = list(self.primary_site.nodes)
        if self.standby_site:
            nodes.extend(self.standby_site.nodes)
        return nodes


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
        sources_path = str(data.get("sources_path", "/u01/sources"))
        patch_version = str(data.get("patch_version", "19.30"))
        primary_site = _parse_site("primary_site", data["primary_site"])
    except KeyError as exc:
        raise ConfigError(f"Missing required config key: {exc.args[0]}") from exc

    standby_site = None
    if data.get("standby_site") is not None:
        standby_site = _parse_site("standby_site", data["standby_site"])

    replication = _parse_replication(data.get("replication", {}))
    os_config = _parse_os(data.get("os", {}))
    ssh = _parse_ssh(data.get("ssh", {}))
    run_id = str(data.get("run_id") or path.stem)

    return AutomationConfig(
        install_type=install_type,
        sources_path=sources_path,
        patch_version=patch_version,
        primary_site=primary_site,
        standby_site=standby_site,
        replication=replication,
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

    return SiteConfig(
        name=str(data.get("name", name)),
        nodes=nodes,
        db_name=_optional_str(data.get("db_name")),
        db_unique_name=_optional_str(data.get("db_unique_name")),
        scan_name=_optional_str(data.get("scan_name")),
        vip_names=[str(item) for item in data.get("vip_names", [])],
        private_interconnects=[str(item) for item in data.get("private_interconnects", [])],
    )


def _parse_node(data: Any, location: str) -> NodeConfig:
    if isinstance(data, str):
        return NodeConfig(host=data)
    if not isinstance(data, dict):
        raise ConfigError(f"{location} must be a hostname string or object.")
    if not data.get("host"):
        raise ConfigError(f"{location}.host is required.")
    return NodeConfig(
        host=str(data["host"]),
        user=_optional_str(data.get("user")),
        role=_optional_str(data.get("role")),
    )


def _parse_replication(data: Any) -> ReplicationConfig:
    if data is None:
        return ReplicationConfig()
    if not isinstance(data, dict):
        raise ConfigError("replication must be an object/mapping.")
    return ReplicationConfig(
        enabled=bool(data.get("enabled", False)),
        mode=str(data.get("mode", "dataguard")),
        protection_mode=str(data.get("protection_mode", "max_performance")),
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
        strict_host_key_checking=str(data.get("strict_host_key_checking", "accept-new")),
    )


def _parse_os(data: Any) -> OSConfig:
    if data is None:
        return OSConfig()
    if not isinstance(data, dict):
        raise ConfigError("os must be an object/mapping.")
    return OSConfig(
        distribution=str(data.get("distribution", "oracle_linux")),
        version=str(data.get("version", "8.10")),
        package_manager=str(data.get("package_manager", "dnf")),
        preinstall_package=str(data.get("preinstall_package", "oracle-database-preinstall-19c")),
    )


def _validate_config(config: AutomationConfig) -> None:
    if config.install_type not in VALID_INSTALL_TYPES:
        raise ConfigError(f"install_type must be one of: {', '.join(sorted(VALID_INSTALL_TYPES))}")
    if not config.sources_path.startswith("/"):
        raise ConfigError("sources_path must be an absolute path on the target server.")
    if config.replication.enabled and not config.standby_site:
        raise ConfigError("standby_site is required when replication.enabled is true.")
    if config.replication.enabled and config.replication.mode != "dataguard":
        raise ConfigError("Only replication.mode=dataguard is supported in this phase.")
    if config.install_type in RAC_INSTALL_TYPES and len(config.primary_site.nodes) < 2:
        raise ConfigError(f"{config.install_type} requires at least two primary_site nodes.")
    if config.standby_site and config.install_type in RAC_INSTALL_TYPES and len(config.standby_site.nodes) < 2:
        raise ConfigError(f"{config.install_type} with standby_site requires at least two standby nodes.")
    if config.os.distribution != "oracle_linux":
        raise ConfigError("Only os.distribution=oracle_linux is supported in this framework baseline.")
    if config.os.version != "8.10":
        raise ConfigError("Only os.version=8.10 is supported in this framework baseline.")
    if config.os.package_manager not in {"dnf", "yum"}:
        raise ConfigError("os.package_manager must be dnf or yum.")

    hosts = [node.host for node in config.all_nodes]
    duplicates = sorted({host for host in hosts if hosts.count(host) > 1})
    if duplicates:
        raise ConfigError(f"Duplicate host(s) in config: {', '.join(duplicates)}")


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
