"""Grid Infrastructure response file manual."""

from __future__ import annotations

from oracle_auto.config import AutomationConfig, SiteConfig
from oracle_auto.phase_builders.common import GRID_BASE_DIR
from oracle_auto.phase_builders.storage import asm_entries


def grid_response(config: AutomationConfig, site: SiteConfig) -> str:
    node_names = ",".join(node.host for node in site.nodes)
    vip_names = ",".join(f"{node.vip_hostname}:{node.vip_ip}" for node in site.nodes if node.vip_ip)
    install_option = "CRS_CONFIG" if config.install_type == "rac" else "HA_CONFIG"
    scan = site.scan_name or ""
    ocr_disks = ",".join("AFD:" + label for label, _path, group, _disk in asm_entries(config) if group == "OCR")
    return f"""oracle.install.responseFileVersion=/oracle/install/rspfmt_crsinstall_response_schema_v19.0.0
INVENTORY_LOCATION=/u01/app/oraInventory
oracle.install.option={install_option}
ORACLE_BASE={GRID_BASE_DIR}
oracle.install.asm.OSDBA=asmdba
oracle.install.asm.OSOPER=asmoper
oracle.install.asm.OSASM=asmadmin
oracle.install.crs.config.clusterName={site.name}-cluster
oracle.install.crs.config.gpnp.scanName={scan}
oracle.install.crs.config.clusterNodes={node_names}
oracle.install.crs.config.networkInterfaceList=
oracle.install.crs.config.configureAsExtendedCluster=false
oracle.install.crs.config.clusterNodeVIPs={vip_names}
oracle.install.asm.diskGroup.name=OCR
oracle.install.asm.diskGroup.redundancy={config.asm.redundancy}
oracle.install.asm.diskGroup.disks={ocr_disks}
oracle.install.asm.configureAFD=true
oracle.install.crs.rootconfig.executeRootScript=false
"""

