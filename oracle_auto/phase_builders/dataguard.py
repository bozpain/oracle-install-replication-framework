"""Active Data Guard phase manual.

Owns primary Data Guard parameter setup, RMAN duplicate, managed recovery, and
Broker configuration when selected.
"""

from __future__ import annotations

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig
from oracle_auto.phase_builders.common import DB_HOME, make_step


def setup_active_dataguard_steps(config: AutomationConfig) -> list[AutomationStep]:
    if not config.standby_site:
        return []
    return [
        make_step(
            "setup-active-dataguard",
            "configure_primary_dataguard",
            config.primary_site.nodes[0],
            "Configure primary database for Active Data Guard",
            _primary_dataguard_script(config),
            timeout=1800,
        ),
        make_step(
            "setup-active-dataguard",
            "duplicate_standby_database",
            config.standby_site.nodes[0],
            "Duplicate standby database from active primary",
            _duplicate_standby_script(config),
            timeout=14400,
        ),
        make_step(
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
        make_step(
            "setup-dataguard-broker",
            "configure_broker",
            config.primary_site.nodes[0],
            "Configure Data Guard Broker",
            _broker_script(config),
            timeout=1800,
        )
    ]


def _primary_dataguard_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_unique = config.primary_site.db_unique_name
    standby_unique = standby.db_unique_name
    lines = [
        _dg_secret_export(config),
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={primary_unique}; sqlplus -s / as sysdba <<'SQL'\nWHENEVER SQLERROR EXIT SQL.SQLCODE\nALTER SYSTEM SET LOG_ARCHIVE_CONFIG='DG_CONFIG=({primary_unique},{standby_unique})' SCOPE=BOTH;\nALTER SYSTEM SET LOG_ARCHIVE_DEST_1='LOCATION=USE_DB_RECOVERY_FILE_DEST VALID_FOR=(ALL_LOGFILES,ALL_ROLES) DB_UNIQUE_NAME={primary_unique}' SCOPE=BOTH;\nALTER SYSTEM SET LOG_ARCHIVE_DEST_2='SERVICE={standby_unique} ASYNC VALID_FOR=(ONLINE_LOGFILES,PRIMARY_ROLE) DB_UNIQUE_NAME={standby_unique}' SCOPE=BOTH;\nALTER SYSTEM SET FAL_SERVER='{standby_unique}' SCOPE=BOTH;\nALTER SYSTEM SET STANDBY_FILE_MANAGEMENT='AUTO' SCOPE=BOTH;\nALTER DATABASE FORCE LOGGING;\nSQL\"",
        f"sudo -iu oracle {DB_HOME}/bin/orapwd file={DB_HOME}/dbs/orapw{primary_unique} force=y format=12 password=\"$DG_PASSWORD\"",
        "echo 'Password file baseline created from target environment secret.'",
    ]
    return shell_script("Configure primary for Active Data Guard", lines)


def _duplicate_standby_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_unique = config.primary_site.db_unique_name
    standby_unique = standby.db_unique_name
    lines = [
        _dg_secret_export(config),
        f"if sudo -iu oracle {DB_HOME}/bin/srvctl config database -db {standby_unique} >/dev/null 2>&1; then",
        f"  echo 'Standby database {standby_unique} already registered in srvctl; skipping RMAN duplicate.'",
        "else",
        f"  sudo -iu oracle bash -lc \"export ORACLE_SID={standby_unique}; {DB_HOME}/bin/rman target sys/\\\"$DG_PASSWORD\\\"@{primary_unique} auxiliary sys/\\\"$DG_PASSWORD\\\"@{standby_unique} <<'RMAN'\nDUPLICATE TARGET DATABASE FOR STANDBY FROM ACTIVE DATABASE DORECOVER NOFILENAMECHECK;\nRMAN\"",
        "fi",
    ]
    return shell_script("Duplicate standby from active primary", lines)


def _start_recovery_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    standby_unique = standby.db_unique_name
    lines = [
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={standby_unique}; sqlplus -s / as sysdba <<'SQL'\nWHENEVER SQLERROR CONTINUE\nALTER DATABASE RECOVER MANAGED STANDBY DATABASE CANCEL;\nALTER DATABASE OPEN READ ONLY;\nALTER DATABASE RECOVER MANAGED STANDBY DATABASE DISCONNECT FROM SESSION;\nSELECT name, open_mode, database_role FROM v\\$database;\nSQL\"",
    ]
    return shell_script("Start Active Data Guard recovery", lines)


def _broker_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_unique = config.primary_site.db_unique_name
    standby_unique = standby.db_unique_name
    broker_name = f"{primary_unique}_{standby_unique}_BROKER"
    lines = [
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={primary_unique}; sqlplus -s / as sysdba <<'SQL'\nWHENEVER SQLERROR EXIT SQL.SQLCODE\nALTER SYSTEM SET DG_BROKER_START=TRUE SCOPE=BOTH;\nSQL\"",
        "set +e",
        "broker_output=$(sudo -iu oracle dgmgrl / <<'DGMGRL'\nSHOW CONFIGURATION;\nDGMGRL\n)",
        "broker_rc=$?",
        "set -e",
        "if test \"$broker_rc\" -eq 0 && printf '%s\\n' \"$broker_output\" | grep -qi 'Configuration -'; then",
        "  echo 'Data Guard Broker configuration already exists; validating existing configuration.'",
        "  printf '%s\\n' \"$broker_output\"",
        f"  sudo -iu oracle dgmgrl / <<'DGMGRL'\nVALIDATE DATABASE '{primary_unique}';\nVALIDATE DATABASE '{standby_unique}';\nSHOW CONFIGURATION;\nDGMGRL",
        "else",
        f"  sudo -iu oracle dgmgrl / <<'DGMGRL'\nCREATE CONFIGURATION '{broker_name}' AS PRIMARY DATABASE IS '{primary_unique}' CONNECT IDENTIFIER IS {primary_unique};\nADD DATABASE '{standby_unique}' AS CONNECT IDENTIFIER IS {standby_unique} MAINTAINED AS PHYSICAL;\nENABLE CONFIGURATION;\nVALIDATE DATABASE '{primary_unique}';\nVALIDATE DATABASE '{standby_unique}';\nSHOW CONFIGURATION;\nDGMGRL",
        "fi",
    ]
    return shell_script("Configure Data Guard Broker", lines)


def _dg_secret_export(config: AutomationConfig) -> str:
    return (
        f'DG_PASSWORD="${{{config.secrets.dg_password_env}:?Set {config.secrets.dg_password_env} on target before running Data Guard steps}}"\n'
        "export DG_PASSWORD"
    )
