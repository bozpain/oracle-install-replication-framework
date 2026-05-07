"""Database software and primary creation phase manual.

Owns DB home installation and DBCA silent database creation. Data Guard-specific
database actions live in `dataguard.py`.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig, SiteConfig
from oracle_auto.phase_builders.common import DB_HOME, ORACLE_BASE, STAGE, make_step


def install_db_software_steps(config: AutomationConfig) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for site in config.sites:
        first = site.nodes[0]
        steps.append(
            make_step(
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
                make_step(
                    "install-db-software",
                    f"db_root_script_{site.name}_{node.short_name}",
                    node,
                    f"Run Database root script for {node.host}",
                    _db_root_script(),
                    timeout=1200,
                )
            )
    return steps


def create_database_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        make_step(
            "create-database",
            "create_primary_database",
            config.primary_site.nodes[0],
            "Create primary database with DBCA silent",
            _create_database_script(config),
            timeout=10800,
        )
    ]


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
    return shell_script("Run Database root script", [f"test -x {DB_HOME}/root.sh", f"{DB_HOME}/root.sh"])


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

