"""Automation phase builder manual.

Each function in this module returns concrete shell steps for one roadmap phase.
The scripts are intentionally generated from typed config so operators adjust
JSON/YAML inputs instead of editing shell by hand. Every script starts with an
operator-facing header through `shell_script()`.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig, NodeConfig, PatchConfig, SiteConfig


GRID_BASE = "/u01/app/19.0.0/grid"
GRID_BASE_DIR = "/u01/app/grid"
ORACLE_BASE = "/u01/app/oracle"
DB_HOME = "/u01/app/oracle/product/19.0.0/dbhome_1"
STAGE = "/u01/stage"


def prepare_os_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        _step(
            "prepare-os",
            "prepare_os",
            node,
            "Prepare Oracle Linux users, DNS, hosts, firewall, SELinux, and chrony",
            _prepare_os_script(config),
            timeout=900,
        )
        for node in config.all_nodes
    ]


def verify_installer_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        _step(
            "verify-installer",
            "verify_installer",
            node,
            "Verify Oracle installer and patch ZIP files",
            _verify_installer_script(config),
            timeout=300,
        )
        for node in config.all_nodes
    ]


def prepare_storage_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        _step(
            "prepare-storage",
            "prepare_asm_storage",
            node,
            "Prepare ASM Filter Driver labels and disk groups",
            _prepare_storage_script(config),
            timeout=1200,
        )
        for node in config.all_nodes
    ]


def install_grid_steps(config: AutomationConfig) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for site in config.sites:
        first = site.nodes[0]
        steps.append(
            _step(
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
                _step(
                    "install-grid",
                    f"root_scripts_{site.name}_{node.short_name}",
                    node,
                    f"Run Grid root scripts for {node.host}",
                    _grid_root_script(),
                    timeout=1800,
                )
            )
    return steps


def install_db_software_steps(config: AutomationConfig) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for site in config.sites:
        first = site.nodes[0]
        steps.append(
            _step(
                "install-db-software",
                f"install_db_home_{site.name}",
                first,
                f"Install Oracle Database software for {site.name}",
                _install_db_software_script(config, site),
                timeout=7200,
            )
        )
        for node in site.nodes:
            steps.append(
                _step(
                    "install-db-software",
                    f"db_root_script_{site.name}_{node.short_name}",
                    node,
                    f"Run Database root script for {node.host}",
                    _db_root_script(),
                    timeout=1200,
                )
            )
    return steps


def apply_patch_steps(config: AutomationConfig) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for node in config.all_nodes:
        steps.append(
            _step(
                "apply-patch",
                "update_opatch",
                node,
                "Update OPatch if OPatch ZIP is configured",
                _update_opatch_script(config),
                timeout=1200,
            )
        )
        for index, patch in enumerate(config.installer.patches, start=1):
            steps.append(
                _step(
                    "apply-patch",
                    f"apply_patch_{index}_{_safe_name(patch.label)}",
                    node,
                    f"Apply patch {patch.label}",
                    _apply_patch_script(config, patch),
                    timeout=7200,
                )
            )
        steps.append(
            _step(
                "apply-patch",
                "patch_inventory",
                node,
                "Collect OPatch inventory",
                _patch_inventory_script(),
                timeout=600,
            )
        )
    return steps


def create_database_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        _step(
            "create-database",
            "create_primary_database",
            config.primary_site.nodes[0],
            "Create primary database with DBCA silent",
            _create_database_script(config),
            timeout=10800,
        )
    ]


def setup_active_dataguard_steps(config: AutomationConfig) -> list[AutomationStep]:
    if not config.standby_site:
        return []
    return [
        _step(
            "setup-active-dataguard",
            "configure_primary_dataguard",
            config.primary_site.nodes[0],
            "Configure primary database for Active Data Guard",
            _primary_dataguard_script(config),
            timeout=1800,
        ),
        _step(
            "setup-active-dataguard",
            "duplicate_standby_database",
            config.standby_site.nodes[0],
            "Duplicate standby database from active primary",
            _duplicate_standby_script(config),
            timeout=14400,
        ),
        _step(
            "setup-active-dataguard",
            "start_managed_recovery",
            config.standby_site.nodes[0],
            "Start Active Data Guard managed recovery",
            _start_recovery_script(config),
            timeout=1800,
        ),
    ]


def setup_dataguard_broker_steps(config: AutomationConfig) -> list[AutomationStep]:
    if not config.standby_site or config.dataguard.configuration_method != "broker":
        return []
    return [
        _step(
            "setup-dataguard-broker",
            "configure_broker",
            config.primary_site.nodes[0],
            "Configure Data Guard Broker",
            _broker_script(config),
            timeout=1800,
        )
    ]


def validate_deployment_steps(config: AutomationConfig) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for node in config.all_nodes:
        steps.append(
            _step(
                "validate-deployment",
                "validate_grid_asm",
                node,
                "Validate Grid Infrastructure and ASM",
                _validate_grid_asm_script(),
                timeout=600,
                warn_only=True,
            )
        )
    steps.append(
        _step(
            "validate-deployment",
            "validate_database",
            config.primary_site.nodes[0],
            "Validate database role, open mode, services, and Data Guard lag",
            _validate_database_script(config),
            timeout=900,
        )
    )
    return steps


def switchover_steps(config: AutomationConfig) -> list[AutomationStep]:
    if not config.standby_site:
        return []
    return [
        _step(
            "switchover",
            "switchover_to_standby",
            config.primary_site.nodes[0],
            "Switchover primary role to standby",
            _switchover_script(config),
            timeout=3600,
        )
    ]


def failover_steps(config: AutomationConfig) -> list[AutomationStep]:
    if not config.standby_site:
        return []
    return [
        _step(
            "failover",
            "failover_to_standby",
            config.standby_site.nodes[0],
            "Failover to standby database",
            _failover_script(config),
            timeout=3600,
        )
    ]


def _prepare_os_script(config: AutomationConfig) -> str:
    hosts_block = _hosts_block(config)
    resolv_conf = _resolv_conf(config)
    chrony_block = "\n".join(f"server {server} iburst" for server in config.os.ntp_servers)
    lines = [
        f"{config.os.package_manager} install -y {shlex.quote(config.os.preinstall_package)} chrony unzip tar libnsl",
        "for group in oinstall dba oper backupdba dgdba kmdba racdba asmadmin asmdba asmoper; do getent group \"$group\" >/dev/null || groupadd \"$group\"; done",
        "id grid >/dev/null 2>&1 || useradd -g oinstall -G asmadmin,asmdba,asmoper,dba grid",
        "id oracle >/dev/null 2>&1 || useradd -g oinstall -G dba,oper,backupdba,dgdba,kmdba,racdba,asmdba oracle",
        f"mkdir -p {GRID_BASE_DIR} {GRID_BASE} {ORACLE_BASE} {DB_HOME} {config.installer.sources_path} {STAGE}",
        f"chown -R grid:oinstall {GRID_BASE_DIR} {GRID_BASE}",
        f"chown -R oracle:oinstall {ORACLE_BASE}",
        f"chmod -R 775 {GRID_BASE_DIR} {ORACLE_BASE}",
        "cp -p /etc/resolv.conf /etc/resolv.conf.oracle-auto.bak.$(date +%Y%m%d%H%M%S) 2>/dev/null || true",
        f"cat > /etc/resolv.conf <<'EOF'\n{resolv_conf}\nEOF",
        "awk '/# BEGIN ORACLE-AUTO HOSTS/{skip=1} /# END ORACLE-AUTO HOSTS/{skip=0; next} !skip{print}' /etc/hosts > /etc/hosts.oracle-auto",
        "cat >> /etc/hosts.oracle-auto <<'EOF'\n# BEGIN ORACLE-AUTO HOSTS\n" + hosts_block + "\n# END ORACLE-AUTO HOSTS\nEOF",
        "cp /etc/hosts /etc/hosts.oracle-auto.bak.$(date +%Y%m%d%H%M%S)",
        "mv /etc/hosts.oracle-auto /etc/hosts",
        "systemctl disable --now firewalld 2>/dev/null || true",
        "systemctl disable --now iptables 2>/dev/null || true",
        "systemctl disable --now nftables 2>/dev/null || true",
        "setenforce 0 2>/dev/null || true",
        "test -f /etc/selinux/config && sed -i 's/^SELINUX=.*/SELINUX=permissive/' /etc/selinux/config || true",
        "cp -p /etc/chrony.conf /etc/chrony.conf.oracle-auto.bak.$(date +%Y%m%d%H%M%S) 2>/dev/null || true",
        "sed -i '/^server /s/^/# oracle-auto disabled /; /^pool /s/^/# oracle-auto disabled /' /etc/chrony.conf 2>/dev/null || true",
        "awk '/# BEGIN ORACLE-AUTO CHRONY/{skip=1} /# END ORACLE-AUTO CHRONY/{skip=0; next} !skip{print}' /etc/chrony.conf > /etc/chrony.conf.oracle-auto",
        "cat >> /etc/chrony.conf.oracle-auto <<'EOF'\n# BEGIN ORACLE-AUTO CHRONY\n" + chrony_block + "\n# END ORACLE-AUTO CHRONY\nEOF",
        "mv /etc/chrony.conf.oracle-auto /etc/chrony.conf",
        "systemctl enable --now chronyd",
        "systemctl restart chronyd",
        "chronyc sources || true",
    ]
    return shell_script("Prepare OS baseline", lines)


def _verify_installer_script(config: AutomationConfig) -> str:
    sources = shlex.quote(config.installer.sources_path)
    files = [config.installer.grid_zip, config.installer.db_zip]
    if config.installer.opatch_zip:
        files.append(config.installer.opatch_zip)
    files.extend(patch.file for patch in config.installer.patches)
    checks = [f"test -s {sources}/{shlex.quote(file)}" for file in files]
    lines = [
        f"test -d {sources}",
        f"test -r {sources}",
        *checks,
        f"sudo -iu grid test -r {sources}/{shlex.quote(config.installer.grid_zip)}",
        f"sudo -iu oracle test -r {sources}/{shlex.quote(config.installer.db_zip)}",
        f"mkdir -p {STAGE}/installer-checks",
        f"ls -lh {sources} > {STAGE}/installer-checks/files.txt",
    ]
    return shell_script("Verify installer ZIP files", lines)


def _prepare_storage_script(config: AutomationConfig) -> str:
    labels = _afd_labels(config)
    disk_checks = [f"test -b {shlex.quote(disk)}" for disk in config.asm.all_disks]
    label_commands = [
        f"asmcmd afd_label {label} {shlex.quote(disk)} --init || asmcmd afd_label {label} {shlex.quote(disk)}"
        for label, disk, _group in labels
    ]
    diskgroup_commands = [
        _create_diskgroup_sql("OCR", [label for label, _disk, group in labels if group == "OCR"], config.asm.redundancy),
        _create_diskgroup_sql("DATA", [label for label, _disk, group in labels if group == "DATA"], config.asm.redundancy),
        _create_diskgroup_sql("RECO", [label for label, _disk, group in labels if group == "RECO"], config.asm.redundancy),
    ]
    lines = [
        *disk_checks,
        "command -v asmcmd",
        "asmcmd afd_state || true",
        *label_commands,
        "asmcmd afd_lslbl || true",
        *diskgroup_commands,
        "sudo -iu grid asmcmd lsdg",
    ]
    return shell_script("Prepare ASM AFD labels and diskgroups", lines)


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


def _install_db_software_script(config: AutomationConfig, site: SiteConfig) -> str:
    response = _db_home_response()
    lines = [
        f"mkdir -p {STAGE}/responses",
        f"test -x {DB_HOME}/runInstaller || sudo -iu oracle unzip -oq {shlex.quote(config.installer.sources_path)}/{shlex.quote(config.installer.db_zip)} -d {DB_HOME}",
        f"cat > {STAGE}/responses/dbhome-{site.name}.rsp <<'EOF'\n{response}\nEOF",
        f"chown oracle:oinstall {STAGE}/responses/dbhome-{site.name}.rsp",
        f"sudo -iu oracle {DB_HOME}/runInstaller -silent -waitforcompletion -responseFile {STAGE}/responses/dbhome-{site.name}.rsp -ignorePrereqFailure",
    ]
    return shell_script(f"Install Database home for {site.name}", lines)


def _db_root_script() -> str:
    lines = [
        f"test -x {DB_HOME}/root.sh",
        f"{DB_HOME}/root.sh",
    ]
    return shell_script("Run Database root script", lines)


def _update_opatch_script(config: AutomationConfig) -> str:
    if not config.installer.opatch_zip:
        return shell_script("Skip OPatch update", ["echo 'No OPatch ZIP configured; skipping.'"])
    opatch_zip = f"{config.installer.sources_path}/{config.installer.opatch_zip}"
    lines = [
        f"test -s {shlex.quote(opatch_zip)}",
        f"mv {GRID_BASE}/OPatch {GRID_BASE}/OPatch.bak.$(date +%Y%m%d%H%M%S) 2>/dev/null || true",
        f"mv {DB_HOME}/OPatch {DB_HOME}/OPatch.bak.$(date +%Y%m%d%H%M%S) 2>/dev/null || true",
        f"sudo -iu grid unzip -oq {shlex.quote(opatch_zip)} -d {GRID_BASE}",
        f"sudo -iu oracle unzip -oq {shlex.quote(opatch_zip)} -d {DB_HOME}",
        f"sudo -iu grid {GRID_BASE}/OPatch/opatch version",
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch version",
    ]
    return shell_script("Update OPatch", lines)


def _apply_patch_script(config: AutomationConfig, patch: PatchConfig) -> str:
    patch_zip = f"{config.installer.sources_path}/{patch.file}"
    patch_dir = f"{STAGE}/patches/{_safe_name(patch.file)}"
    lines = [
        f"test -s {shlex.quote(patch_zip)}",
        f"mkdir -p {patch_dir}",
        f"unzip -oq {shlex.quote(patch_zip)} -d {patch_dir}",
        f"PATCH_TOP=$(find {patch_dir} -mindepth 1 -maxdepth 1 -type d | head -1)",
        "test -n \"$PATCH_TOP\"",
        f"{GRID_BASE}/OPatch/opatchauto apply \"$PATCH_TOP\" || sudo -iu oracle {DB_HOME}/OPatch/opatch apply -silent \"$PATCH_TOP\"",
    ]
    return shell_script(f"Apply patch {patch.label}", lines)


def _patch_inventory_script() -> str:
    lines = [
        f"sudo -iu grid {GRID_BASE}/OPatch/opatch lsinventory || true",
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch lsinventory",
    ]
    return shell_script("Collect patch inventory", lines)


def _create_database_script(config: AutomationConfig) -> str:
    db_name = config.primary_site.db_name or config.primary_site.db_unique_name
    unique = config.primary_site.db_unique_name
    response = _dbca_response(config, db_name, unique)
    lines = [
        f"mkdir -p {STAGE}/responses",
        f"cat > {STAGE}/responses/dbca-primary.rsp <<'EOF'\n{response}\nEOF",
        f"chown oracle:oinstall {STAGE}/responses/dbca-primary.rsp",
        f"sudo -iu oracle {DB_HOME}/bin/dbca -silent -createDatabase -responseFile {STAGE}/responses/dbca-primary.rsp",
        f"sudo -iu oracle {DB_HOME}/bin/srvctl status database -db {unique} || true",
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={unique}; sqlplus -s / as sysdba <<'SQL'\nALTER DATABASE FORCE LOGGING;\nARCHIVE LOG LIST;\nSELECT name, open_mode, database_role FROM v\\$database;\nSQL\"",
    ]
    return shell_script("Create primary database", lines)


def _primary_dataguard_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_unique = config.primary_site.db_unique_name
    standby_unique = standby.db_unique_name
    lines = [
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={primary_unique}; sqlplus -s / as sysdba <<'SQL'\nALTER SYSTEM SET LOG_ARCHIVE_CONFIG='DG_CONFIG=({primary_unique},{standby_unique})' SCOPE=BOTH;\nALTER SYSTEM SET LOG_ARCHIVE_DEST_1='LOCATION=USE_DB_RECOVERY_FILE_DEST VALID_FOR=(ALL_LOGFILES,ALL_ROLES) DB_UNIQUE_NAME={primary_unique}' SCOPE=BOTH;\nALTER SYSTEM SET LOG_ARCHIVE_DEST_2='SERVICE={standby_unique} ASYNC VALID_FOR=(ONLINE_LOGFILES,PRIMARY_ROLE) DB_UNIQUE_NAME={standby_unique}' SCOPE=BOTH;\nALTER SYSTEM SET FAL_SERVER='{standby_unique}' SCOPE=BOTH;\nALTER SYSTEM SET STANDBY_FILE_MANAGEMENT='AUTO' SCOPE=BOTH;\nALTER DATABASE FORCE LOGGING;\nSQL\"",
        f"sudo -iu oracle {DB_HOME}/bin/orapwd file={DB_HOME}/dbs/orapw{primary_unique} force=y format=12 password=Oracle_Change_Me_1",
        "echo 'Password file baseline created; replace generated password through secure secret flow before production.'",
    ]
    return shell_script("Configure primary for Active Data Guard", lines)


def _duplicate_standby_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_unique = config.primary_site.db_unique_name
    standby_unique = standby.db_unique_name
    lines = [
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={standby_unique}; {DB_HOME}/bin/rman target sys/Oracle_Change_Me_1@{primary_unique} auxiliary sys/Oracle_Change_Me_1@{standby_unique} <<'RMAN'\nDUPLICATE TARGET DATABASE FOR STANDBY FROM ACTIVE DATABASE DORECOVER NOFILENAMECHECK;\nRMAN\"",
    ]
    return shell_script("Duplicate standby from active primary", lines)


def _start_recovery_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    standby_unique = standby.db_unique_name
    lines = [
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={standby_unique}; sqlplus -s / as sysdba <<'SQL'\nALTER DATABASE RECOVER MANAGED STANDBY DATABASE CANCEL;\nALTER DATABASE OPEN READ ONLY;\nALTER DATABASE RECOVER MANAGED STANDBY DATABASE DISCONNECT FROM SESSION;\nSELECT name, open_mode, database_role FROM v\\$database;\nSQL\"",
    ]
    return shell_script("Start Active Data Guard recovery", lines)


def _broker_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_unique = config.primary_site.db_unique_name
    standby_unique = standby.db_unique_name
    broker_name = f"{primary_unique}_{standby_unique}_BROKER"
    lines = [
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={primary_unique}; sqlplus -s / as sysdba <<'SQL'\nALTER SYSTEM SET DG_BROKER_START=TRUE SCOPE=BOTH;\nSQL\"",
        f"sudo -iu oracle dgmgrl / <<'DGMGRL'\nCREATE CONFIGURATION '{broker_name}' AS PRIMARY DATABASE IS '{primary_unique}' CONNECT IDENTIFIER IS {primary_unique};\nADD DATABASE '{standby_unique}' AS CONNECT IDENTIFIER IS {standby_unique} MAINTAINED AS PHYSICAL;\nENABLE CONFIGURATION;\nVALIDATE DATABASE '{primary_unique}';\nVALIDATE DATABASE '{standby_unique}';\nSHOW CONFIGURATION;\nDGMGRL",
    ]
    return shell_script("Configure Data Guard Broker", lines)


def _validate_grid_asm_script() -> str:
    lines = [
        f"sudo -iu grid {GRID_BASE}/bin/crsctl check crs",
        f"sudo -iu grid {GRID_BASE}/bin/crsctl stat res -t",
        "sudo -iu grid asmcmd lsdg",
    ]
    return shell_script("Validate Grid and ASM", lines)


def _validate_database_script(config: AutomationConfig) -> str:
    primary_unique = config.primary_site.db_unique_name
    dg_sql = ""
    if config.standby_site:
        dg_sql = "SELECT name, value, unit FROM v\\$dataguard_stats;"
    lines = [
        f"sudo -iu oracle {DB_HOME}/bin/srvctl status database -db {primary_unique} || true",
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={primary_unique}; sqlplus -s / as sysdba <<'SQL'\nSELECT name, open_mode, database_role FROM v\\$database;\n{dg_sql}\nSQL\"",
    ]
    return shell_script("Validate database", lines)


def _switchover_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_unique = config.primary_site.db_unique_name
    standby_unique = standby.db_unique_name
    if config.dataguard.configuration_method == "broker":
        lines = [
            f"sudo -iu oracle dgmgrl / <<'DGMGRL'\nVALIDATE DATABASE '{primary_unique}';\nVALIDATE DATABASE '{standby_unique}';\nSWITCHOVER TO '{standby_unique}';\nSHOW CONFIGURATION;\nDGMGRL",
        ]
    else:
        lines = [
            f"sudo -iu oracle bash -lc \"export ORACLE_SID={primary_unique}; sqlplus -s / as sysdba <<'SQL'\nALTER DATABASE COMMIT TO SWITCHOVER TO PHYSICAL STANDBY WITH SESSION SHUTDOWN;\nSHUTDOWN IMMEDIATE;\nSTARTUP MOUNT;\nSQL\"",
            f"echo 'Complete manual switchover activation on standby {standby_unique} from standby node if required by Oracle role state.'",
        ]
    return shell_script("Switchover to standby", lines)


def _failover_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    standby_unique = standby.db_unique_name
    if config.dataguard.configuration_method == "broker":
        lines = [
            f"sudo -iu oracle dgmgrl / <<'DGMGRL'\nFAILOVER TO '{standby_unique}' IMMEDIATE;\nSHOW CONFIGURATION;\nDGMGRL",
            "echo 'Former primary must be reinstated or rebuilt after failover.'",
        ]
    else:
        lines = [
            f"sudo -iu oracle bash -lc \"export ORACLE_SID={standby_unique}; sqlplus -s / as sysdba <<'SQL'\nALTER DATABASE RECOVER MANAGED STANDBY DATABASE FINISH FORCE;\nALTER DATABASE ACTIVATE STANDBY DATABASE;\nALTER DATABASE OPEN;\nSELECT name, open_mode, database_role FROM v\\$database;\nSQL\"",
            "echo 'Former primary must be rebuilt or manually reinstated after failover.'",
        ]
    return shell_script("Failover to standby", lines)


def _grid_response(config: AutomationConfig, site: SiteConfig) -> str:
    node_names = ",".join(node.host for node in site.nodes)
    vip_names = ",".join(f"{node.vip_hostname}:{node.vip_ip}" for node in site.nodes if node.vip_ip)
    install_option = "CRS_CONFIG" if config.install_type == "rac" else "HA_CONFIG"
    scan = site.scan_name or ""
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
oracle.install.asm.diskGroup.disks={','.join('AFD:' + label for label, _disk, group in _afd_labels(config) if group == 'OCR')}
oracle.install.asm.configureAFD=true
oracle.install.crs.rootconfig.executeRootScript=false
"""


def _db_home_response() -> str:
    return f"""oracle.install.responseFileVersion=/oracle/install/rspfmt_dbinstall_response_schema_v19.0.0
oracle.install.option=INSTALL_DB_SWONLY
UNIX_GROUP_NAME=oinstall
INVENTORY_LOCATION=/u01/app/oraInventory
ORACLE_HOME={DB_HOME}
ORACLE_BASE={ORACLE_BASE}
oracle.install.db.InstallEdition=EE
oracle.install.db.OSDBA_GROUP=dba
oracle.install.db.OSOPER_GROUP=oper
oracle.install.db.OSBACKUPDBA_GROUP=backupdba
oracle.install.db.OSDGDBA_GROUP=dgdba
oracle.install.db.OSKMDBA_GROUP=kmdba
oracle.install.db.OSRACDBA_GROUP=racdba
DECLINE_SECURITY_UPDATES=true
"""


def _dbca_response(config: AutomationConfig, db_name: str, unique_name: str) -> str:
    node_list = ",".join(node.host for node in config.primary_site.nodes)
    database_type = "RAC" if config.install_type == "rac" else "SINGLE"
    return f"""responseFileVersion=/oracle/assistants/rspfmt_dbca_response_schema_v19.0.0
gdbName={db_name}
sid={unique_name}
databaseConfigType={database_type}
RACOneNodeServiceName=
policyManaged=false
createServerPool=false
force=false
createAsContainerDatabase=false
templateName=General_Purpose.dbc
sysPassword=Oracle_Change_Me_1
systemPassword=Oracle_Change_Me_1
emConfiguration=NONE
datafileDestination=+DATA
recoveryAreaDestination=+RECO
storageType=ASM
diskGroupName=DATA
asmsnmpPassword=Oracle_Change_Me_1
characterSet=AL32UTF8
nationalCharacterSet=AL16UTF16
databaseType=MULTIPURPOSE
automaticMemoryManagement=false
totalMemory=4096
nodelist={node_list}
variables=DB_UNIQUE_NAME={unique_name}
"""


def _create_diskgroup_sql(name: str, labels: list[str], redundancy: str) -> str:
    disk_list = ",".join(f"'AFD:{label}'" for label in labels)
    return (
        "sudo -iu grid sqlplus -s / as sysasm <<'SQL'\n"
        "WHENEVER SQLERROR EXIT SQL.SQLCODE\n"
        f"DECLARE\n  existing NUMBER;\nBEGIN\n  SELECT COUNT(*) INTO existing FROM v$asm_diskgroup WHERE name = '{name}';\n"
        "  IF existing = 0 THEN\n"
        f"    EXECUTE IMMEDIATE q'[CREATE DISKGROUP {name} {redundancy} REDUNDANCY DISK {disk_list} ATTRIBUTE 'compatible.asm'='19.0','compatible.rdbms'='19.0','compatible.advm'='19.0']';\n"
        "  END IF;\nEND;\n/\nSQL"
    )


def _hosts_block(config: AutomationConfig) -> str:
    entries: list[str] = []
    for node in config.all_nodes:
        entries.append(_hosts_line(node.public_ip, node.host))
        if node.private_ip:
            entries.append(_hosts_line(node.private_ip, node.private_hostname))
        if node.vip_ip:
            entries.append(_hosts_line(node.vip_ip, node.vip_hostname))
    return "\n".join(entries)


def _hosts_line(ip: str, hostname: str) -> str:
    short = hostname.split(".", 1)[0]
    return f"{ip} {hostname} {short}"


def _resolv_conf(config: AutomationConfig) -> str:
    lines = ["# Generated by oracle-auto"]
    for resolver in config.dns.resolvers:
        lines.append(f"nameserver {resolver}")
    if config.dns.search_domains:
        lines.append("search " + " ".join(config.dns.search_domains))
    return "\n".join(lines)


def _afd_labels(config: AutomationConfig) -> list[tuple[str, str, str]]:
    labels: list[tuple[str, str, str]] = []
    for group, disks in (
        ("OCR", config.asm.ocr_disks),
        ("DATA", config.asm.data_disks),
        ("RECO", config.asm.reco_disks),
    ):
        for index, disk in enumerate(disks, start=1):
            labels.append((f"{group}{index:02d}", disk, group))
    return labels


def _step(
    phase: str,
    name: str,
    node: NodeConfig,
    title: str,
    command: str,
    timeout: int,
    warn_only: bool = False,
) -> AutomationStep:
    return AutomationStep(
        phase=phase,
        name=name,
        node=node,
        title=title,
        command=command,
        timeout=timeout,
        warn_only=warn_only,
    )


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_").lower()
