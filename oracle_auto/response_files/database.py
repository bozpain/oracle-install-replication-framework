"""Database response file manual."""

from __future__ import annotations

from oracle_auto.config import AutomationConfig
from oracle_auto.phase_builders.common import DB_HOME, ORACLE_BASE


def db_home_response() -> str:
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


def dbca_response(config: AutomationConfig, db_name: str, unique_name: str) -> str:
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
sysPassword=$SYS_PASSWORD
systemPassword=$SYSTEM_PASSWORD
emConfiguration=NONE
datafileDestination=+DATA
recoveryAreaDestination=+RECO
storageType=ASM
diskGroupName=DATA
asmsnmpPassword=$ASMSNMP_PASSWORD
characterSet=AL32UTF8
nationalCharacterSet=AL16UTF16
databaseType=MULTIPURPOSE
automaticMemoryManagement=false
totalMemory=4096
nodelist={node_list}
variables=DB_UNIQUE_NAME={unique_name}
"""

