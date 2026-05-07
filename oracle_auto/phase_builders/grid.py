"""Grid Infrastructure phase manual.

Builds Grid Infrastructure response files and root script execution steps. RAC
and single-GI share this module because both use GI and ASM.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig, SiteConfig
from oracle_auto.phase_builders.common import GRID_BASE, GRID_BASE_DIR, STAGE, make_step
from oracle_auto.phase_builders.storage import asm_entries


def install_grid_steps(config: AutomationConfig) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for site in config.sites:
        first = site.nodes[0]
        steps.append(
            make_step(
                "install-grid",
                f"install_grid_{site.name}",
                first,
                f"Install Grid Infrastructure for {site.name}",
                _install_grid_script(config, site),
                timeout=7200,
            )
        )
        for node in site.nodes:
            steps.append(
                make_step(
                    "install-grid",
                    f"root_scripts_{site.name}_{node.short_name}",
                    node,
                    f"Run Grid root scripts for {node.host}",
                    _grid_root_script(),
                    timeout=1800,
                )
            )
    return steps


def _install_grid_script(config: AutomationConfig, site: SiteConfig) -> str:
    response = _grid_response(config, site)
    lines = [
        f"mkdir -p {STAGE}/responses",
        f"test -x {GRID_BASE}/gridSetup.sh || sudo -iu grid unzip -oq {shlex.quote(config.installer.sources_path)}/{shlex.quote(config.installer.grid_zip)} -d {GRID_BASE}",
        f"cat > {STAGE}/responses/grid-{site.name}.rsp <<'EOF'\n{response}\nEOF",
        f"chown grid:oinstall {STAGE}/responses/grid-{site.name}.rsp",
        f"sudo -iu grid {GRID_BASE}/gridSetup.sh -silent -waitforcompletion -responseFile {STAGE}/responses/grid-{site.name}.rsp -ignorePrereqFailure",
    ]
    return shell_script(f"Install Grid Infrastructure for {site.name}", lines)


def _grid_root_script() -> str:
    lines = [
        f"test -x {GRID_BASE}/root.sh",
        f"{GRID_BASE}/root.sh",
        f"sudo -iu grid {GRID_BASE}/bin/crsctl check crs || true",
        "sudo -iu grid asmcmd lsdg || true",
    ]
    return shell_script("Run Grid root scripts", lines)


def _grid_response(config: AutomationConfig, site: SiteConfig) -> str:
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

