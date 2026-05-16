"""Grid Infrastructure response file manual."""

from __future__ import annotations

from oracle_auto.config import AutomationConfig, SiteConfig
from oracle_auto.phase_builders.common import GRID_BASE_DIR
from oracle_auto.phase_builders.storage import asm_discovery_string, asm_disk_spec, asm_entries


def grid_response(config: AutomationConfig, site: SiteConfig) -> str:
    install_option = "CRS_CONFIG" if config.install_type == "rac" else "HA_CONFIG"
    initial_group = "OCR" if config.install_type == "rac" else "DATA"
    initial_disks = ",".join(
        asm_disk_spec(label)
        for label, _path, group, _disk in asm_entries(config)
        if group == initial_group
    )
    lines = [
        "oracle.install.responseFileVersion=/oracle/install/rspfmt_crsinstall_response_schema_v19.0.0",
        "INVENTORY_LOCATION=/u01/app/oraInventory",
        f"oracle.install.option={install_option}",
        f"ORACLE_BASE={GRID_BASE_DIR}",
        "oracle.install.asm.OSDBA=asmdba",
        "oracle.install.asm.OSOPER=asmoper",
        "oracle.install.asm.OSASM=asmadmin",
        *(_cluster_lines(site) if config.install_type == "rac" else []),
        "oracle.install.asm.SYSASMPassword=$ASMSNMP_PASSWORD",
        f"oracle.install.asm.diskGroup.name={initial_group}",
        f"oracle.install.asm.diskGroup.redundancy={config.asm.redundancy}",
        f"oracle.install.asm.diskGroup.disks={initial_disks}",
        f"oracle.install.asm.diskGroup.diskDiscoveryString={asm_discovery_string(config)}",
        "oracle.install.asm.monitorPassword=$ASMSNMP_PASSWORD",
        "oracle.install.config.managementOption=NONE",
        "oracle.install.crs.rootconfig.executeRootScript=false",
    ]
    return "\n".join(lines) + "\n"


def _cluster_lines(site: SiteConfig) -> list[str]:
    node_names = ",".join(
        f"{node.host}:{node.vip_hostname}" if node.vip_ip else node.host
        for node in site.nodes
    )
    return [
        "oracle.install.crs.config.scanType=LOCAL_SCAN",
        "oracle.install.crs.config.SCANClientDataFile=",
        f"oracle.install.crs.config.gpnp.scanName={site.scan_name or ''}",
        "oracle.install.crs.config.gpnp.scanPort=1521",
        "oracle.install.crs.config.ClusterConfiguration=STANDALONE",
        "oracle.install.crs.config.configureAsExtendedCluster=false",
        "oracle.install.crs.config.memberClusterManifestFile=",
        f"oracle.install.crs.config.clusterName={site.name}-cluster",
        "oracle.install.crs.config.gpnp.configureGNS=false",
        "oracle.install.crs.config.autoConfigureClusterNodeVIP=false",
        "oracle.install.crs.config.gpnp.gnsOption=",
        "oracle.install.crs.config.gpnp.gnsClientDataFile=",
        "oracle.install.crs.config.gpnp.gnsSubDomain=",
        "oracle.install.crs.config.gpnp.gnsVIPAddress=",
        "oracle.install.crs.config.sites=",
        f"oracle.install.crs.config.clusterNodes={node_names}",
        "oracle.install.crs.config.networkInterfaceList=",
        "oracle.install.crs.configureGIMR=false",
        "oracle.install.asm.configureGIMRDataDG=false",
        "oracle.install.crs.config.storageOption=FLEX_ASM_STORAGE",
        "oracle.install.crs.config.sharedFileSystemStorage.votingDiskLocations=",
        "oracle.install.crs.config.sharedFileSystemStorage.ocrLocations=",
        "oracle.install.crs.config.useIPMI=false",
        "oracle.install.crs.config.ipmi.bmcUsername=",
        "oracle.install.crs.config.ipmi.bmcPassword=",
    ]
