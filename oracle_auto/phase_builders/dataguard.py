"""Data Guard phase manual.

Owns primary Data Guard parameter setup, RMAN duplicate, managed recovery, and
Broker configuration when selected.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig
from oracle_auto.phase_builders.common import DB_HOME, GRID_BASE, ORACLE_BASE, make_step


def configure_dataguard_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        *_dataguard_network_steps(config, "configure-dataguard"),
        *_dataguard_network_validation_steps(config, "configure-dataguard"),
        *_physical_standby_steps(config, "configure-dataguard"),
        *_broker_steps(config, "configure-dataguard"),
    ]


def _dataguard_network_steps(config: AutomationConfig, phase: str) -> list[AutomationStep]:
    if not config.standby_site:
        return []
    return [
        make_step(
            phase,
            "configure_dataguard_network",
            node,
            "Configure Data Guard network aliases and static listener",
            _dataguard_network_script(config, node.host),
            timeout=1800,
        )
        for node in config.all_nodes
    ]


def _dataguard_network_validation_steps(config: AutomationConfig, phase: str) -> list[AutomationStep]:
    if not config.standby_site:
        return []
    return [
        make_step(
            phase,
            "validate_dataguard_network",
            node,
            "Validate Data Guard network aliases",
            _dataguard_network_validation_script(config),
            timeout=900,
        )
        for node in config.all_nodes
    ]


def _dataguard_final_network_validation_steps(config: AutomationConfig, phase: str) -> list[AutomationStep]:
    if not config.standby_site:
        return []
    return [
        make_step(
            phase,
            "validate_dataguard_final_network",
            node,
            "Validate final Data Guard network aliases",
            _dataguard_network_validation_script(config),
            timeout=900,
        )
        for node in config.all_nodes
    ]


def _physical_standby_steps(config: AutomationConfig, phase: str) -> list[AutomationStep]:
    if not config.standby_site:
        return []
    return [
        make_step(
            phase,
            "configure_primary_dataguard",
            config.primary_site.nodes[0],
            "Configure primary database for Active Data Guard",
            _primary_dataguard_script(config),
            timeout=1800,
        ),
        make_step(
            phase,
            "prepare_standby_auxiliary",
            config.standby_site.nodes[0],
            "Prepare standby auxiliary instance for RMAN duplicate",
            _prepare_standby_auxiliary_script(config),
            timeout=1800,
        ),
        make_step(
            phase,
            "duplicate_standby_database",
            config.standby_site.nodes[0],
            "Duplicate standby database from active primary",
            _duplicate_standby_script(config),
            timeout=14400,
        ),
        *_dataguard_final_network_steps(config, phase),
        *_dataguard_final_network_validation_steps(config, phase),
        make_step(
            phase,
            "start_managed_recovery",
            config.standby_site.nodes[0],
            "Start Active Data Guard managed recovery",
            _start_recovery_script(config),
            timeout=1800,
        ),
        make_step(
            phase,
            "verify_primary_dataguard",
            config.primary_site.nodes[0],
            "Verify primary Data Guard transport",
            _verify_primary_dataguard_script(config),
            timeout=1800,
        ),
        make_step(
            phase,
            "verify_standby_dataguard",
            config.standby_site.nodes[0],
            "Verify standby Data Guard apply",
            _verify_standby_dataguard_script(config),
            timeout=1800,
        ),
    ]


def _dataguard_network_script(config: AutomationConfig, node_host: str) -> str:
    return _dataguard_network_script_for(config, node_host, final=False)


def _dataguard_final_network_steps(config: AutomationConfig, phase: str) -> list[AutomationStep]:
    if not config.standby_site:
        return []
    return [
        make_step(
            phase,
            "configure_dataguard_final_network",
            node,
            "Configure final Data Guard network aliases",
            _dataguard_network_script_for(config, node.host, final=True),
            timeout=1800,
        )
        for node in config.all_nodes
    ]


def _dataguard_network_script_for(config: AutomationConfig, node_host: str, *, final: bool) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_unique = config.primary_site.db_unique_name
    standby_unique = standby.db_unique_name
    primary_host = _dataguard_endpoint(config, config.primary_site, final=final)
    standby_host = _dataguard_endpoint(config, standby, final=final)
    local_node = next(node for node in config.all_nodes if node.host == node_host)
    local_site = config.site_for_node(local_node)
    local_unique = local_site.db_unique_name
    local_sid = _instance_name(local_site, local_site.nodes.index(local_node), config.install_type)
    local_host = node_host
    tnsnames = _tnsnames_content(primary_unique, primary_host, standby_unique, standby_host)
    listener_sid = _listener_sid_content(local_unique, local_sid)
    listener_address = _listener_address_content(local_host)
    title = "Configure final Data Guard network" if final else "Configure Data Guard network"
    lines = [
        f"mkdir -p {GRID_BASE}/network/admin {DB_HOME}/network/admin",
        f"cat > {DB_HOME}/network/admin/tnsnames.ora <<'EOF'\n{tnsnames}\nEOF",
        f"cp {DB_HOME}/network/admin/tnsnames.ora {GRID_BASE}/network/admin/tnsnames.ora",
        f"chown -R oracle:oinstall {DB_HOME}/network",
        f"chown -R grid:oinstall {GRID_BASE}/network",
        f"listener_file={shlex.quote(f'{GRID_BASE}/network/admin/listener.ora')}",
        'if test -f "$listener_file"; then',
        '  awk \'/# BEGIN ORACLE-AUTO DATAGUARD/{skip=1} /# END ORACLE-AUTO DATAGUARD/{skip=0; next} !skip{print}\' "$listener_file" > "$listener_file.tmp"',
        '  mv "$listener_file.tmp" "$listener_file"',
        "fi",
        'if grep -qi "^[[:space:]]*LISTENER[[:space:]]*=" "$listener_file"; then',
        f"  cat >> \"$listener_file\" <<'EOF'\n{listener_sid}\nEOF",
        "else",
        f"  cat >> \"$listener_file\" <<'EOF'\n{listener_sid}\n\n{listener_address}\nEOF",
        "fi",
        'chown grid:oinstall "$listener_file"',
        'chmod 664 "$listener_file"',
        f"sudo -iu grid env ORACLE_HOME={GRID_BASE} TNS_ADMIN={GRID_BASE}/network/admin {GRID_BASE}/bin/lsnrctl status LISTENER >/tmp/oracle-auto-listener.status 2>&1 "
        f"&& sudo -iu grid env ORACLE_HOME={GRID_BASE} TNS_ADMIN={GRID_BASE}/network/admin {GRID_BASE}/bin/lsnrctl reload LISTENER "
        f"|| sudo -iu grid env ORACLE_HOME={GRID_BASE} TNS_ADMIN={GRID_BASE}/network/admin {GRID_BASE}/bin/lsnrctl start LISTENER",
        f"sudo -iu grid env ORACLE_HOME={GRID_BASE} TNS_ADMIN={GRID_BASE}/network/admin {GRID_BASE}/bin/lsnrctl status LISTENER",
    ]
    return shell_script(title, lines)


def _dataguard_network_validation_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_unique = config.primary_site.db_unique_name
    standby_unique = standby.db_unique_name
    lines = [
        f"sudo -iu oracle env ORACLE_HOME={DB_HOME} TNS_ADMIN={DB_HOME}/network/admin {DB_HOME}/bin/tnsping {primary_unique}",
        f"sudo -iu oracle env ORACLE_HOME={DB_HOME} TNS_ADMIN={DB_HOME}/network/admin {DB_HOME}/bin/tnsping {standby_unique}",
    ]
    return shell_script("Validate Data Guard network", lines)


def _broker_steps(config: AutomationConfig, phase: str) -> list[AutomationStep]:
    if not config.standby_site or config.dataguard.configuration_method != "broker":
        return []
    return [
        make_step(
            phase,
            "configure_broker",
            config.primary_site.nodes[0],
            "Configure Data Guard Broker",
            _broker_script(config),
            timeout=1800,
        )
    ]


def _prepare_standby_auxiliary_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_db_name = config.primary_site.db_name or config.primary_site.db_unique_name
    standby_unique = standby.db_unique_name
    standby_sid = _instance_name(standby, 0, config.install_type)
    standby_host = standby.nodes[0].host
    pfile = _standby_pfile_content(primary_db_name, standby_unique, standby_host)
    instance_lines = _srvctl_instance_lines(standby, standby_unique, config.install_type)
    lines = [
        _dg_secret_export(config),
        f"mkdir -p {ORACLE_BASE}/admin/{standby_unique}/adump {DB_HOME}/dbs {GRID_BASE}/dbs",
        f"chown -R oracle:oinstall {ORACLE_BASE}/admin/{standby_unique}",
        f"sudo -iu oracle {DB_HOME}/bin/orapwd file={DB_HOME}/dbs/orapw{standby_unique} force=y format=12 password=\"$DG_PASSWORD\"",
        f"cp {DB_HOME}/dbs/orapw{standby_unique} {GRID_BASE}/dbs/orapw{standby_unique}",
        f"cat > {DB_HOME}/dbs/init{standby_unique}.ora <<'EOF'\n{pfile}\nEOF",
        f"chown oracle:oinstall {DB_HOME}/dbs/init{standby_unique}.ora {DB_HOME}/dbs/orapw{standby_unique}",
        f"chmod 600 {DB_HOME}/dbs/init{standby_unique}.ora {DB_HOME}/dbs/orapw{standby_unique}",
        f"chown grid:oinstall {GRID_BASE}/dbs/orapw{standby_unique}",
        f"chmod 600 {GRID_BASE}/dbs/orapw{standby_unique}",
        f"sudo -iu grid {GRID_BASE}/bin/asmcmd mkdir +DATA/{standby_unique} || true",
        f"sudo -iu grid {GRID_BASE}/bin/asmcmd mkdir +DATA/{standby_unique}/PARAMETERFILE || true",
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={standby_sid}; sqlplus -s / as sysdba <<'SQL'\nWHENEVER SQLERROR CONTINUE\nSHUTDOWN ABORT;\nWHENEVER SQLERROR EXIT SQL.SQLCODE\nSTARTUP NOMOUNT PFILE='{DB_HOME}/dbs/init{standby_unique}.ora';\nCREATE SPFILE='+DATA/{standby_unique}/PARAMETERFILE/spfile{standby_unique}.ora' FROM PFILE='{DB_HOME}/dbs/init{standby_unique}.ora';\nSHUTDOWN IMMEDIATE;\nSQL\"",
        f"printf \"SPFILE='+DATA/{standby_unique}/PARAMETERFILE/spfile{standby_unique}.ora'\\n\" > {DB_HOME}/dbs/init{standby_unique}.ora",
        f"chown oracle:oinstall {DB_HOME}/dbs/init{standby_unique}.ora",
        f"chmod 600 {DB_HOME}/dbs/init{standby_unique}.ora",
        f"if sudo -iu oracle {DB_HOME}/bin/srvctl config database -db {standby_unique} >/dev/null 2>&1; then",
        f"  sudo -iu oracle {DB_HOME}/bin/srvctl modify database -db {standby_unique} -spfile +DATA/{standby_unique}/PARAMETERFILE/spfile{standby_unique}.ora -pwfile {DB_HOME}/dbs/orapw{standby_unique} -startoption MOUNT || true",
        "else",
        f"  sudo -iu oracle {DB_HOME}/bin/srvctl add database -db {standby_unique} -dbname {primary_db_name} -oraclehome {DB_HOME} -role PHYSICAL_STANDBY -startoption MOUNT -spfile +DATA/{standby_unique}/PARAMETERFILE/spfile{standby_unique}.ora -pwfile {DB_HOME}/dbs/orapw{standby_unique} -diskgroup DATA,RECO",
        "fi",
        *instance_lines,
        f"sudo -iu oracle {DB_HOME}/bin/srvctl stop database -db {standby_unique} -stopoption ABORT || true",
        f"sudo -iu oracle {DB_HOME}/bin/srvctl start instance -db {standby_unique} -instance {standby_sid} -startoption NOMOUNT || sudo -iu oracle {DB_HOME}/bin/srvctl start database -db {standby_unique} -startoption NOMOUNT",
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={standby_sid}; sqlplus -s / as sysdba <<'SQL'\nWHENEVER SQLERROR EXIT SQL.SQLCODE\nSELECT instance_name, status FROM v\\$instance;\nSQL\"",
    ]
    return shell_script("Prepare standby auxiliary instance", lines)


def _primary_dataguard_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_unique = config.primary_site.db_unique_name
    primary_sid = _instance_name(config.primary_site, 0, config.install_type)
    standby_unique = standby.db_unique_name
    lines = [
        _dg_secret_export(config),
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={primary_sid}; sqlplus -s / as sysdba <<'SQL'\nWHENEVER SQLERROR EXIT SQL.SQLCODE\nALTER DATABASE FORCE LOGGING;\nALTER DATABASE ADD SUPPLEMENTAL LOG DATA;\nSET SERVEROUTPUT ON\nDECLARE\n  l_existing NUMBER;\nBEGIN\n  FOR thread_rec IN (SELECT thread# FROM v\\$thread WHERE enabled = 'PUBLIC' ORDER BY thread#) LOOP\n    SELECT COUNT(*) INTO l_existing FROM v\\$standby_log WHERE thread# = thread_rec.thread#;\n    FOR item IN (l_existing + 1)..4 LOOP\n      EXECUTE IMMEDIATE 'ALTER DATABASE ADD STANDBY LOGFILE THREAD ' || thread_rec.thread# || ' SIZE 2G';\n      DBMS_OUTPUT.PUT_LINE('Added standby redo log for thread ' || thread_rec.thread# || ', slot ' || item);\n    END LOOP;\n  END LOOP;\nEND;\n/\nALTER SYSTEM SET LOG_ARCHIVE_CONFIG='DG_CONFIG=({primary_unique},{standby_unique})' SCOPE=BOTH SID='*';\nALTER SYSTEM SET LOG_ARCHIVE_DEST_1='LOCATION=USE_DB_RECOVERY_FILE_DEST VALID_FOR=(ALL_LOGFILES,ALL_ROLES) DB_UNIQUE_NAME={primary_unique}' SCOPE=BOTH SID='*';\nALTER SYSTEM SET LOG_ARCHIVE_DEST_2='SERVICE={standby_unique} ASYNC VALID_FOR=(ONLINE_LOGFILES,PRIMARY_ROLE) DB_UNIQUE_NAME={standby_unique}' SCOPE=BOTH SID='*';\nALTER SYSTEM SET FAL_SERVER='{standby_unique}' SCOPE=BOTH SID='*';\nALTER SYSTEM SET STANDBY_FILE_MANAGEMENT='AUTO' SCOPE=BOTH SID='*';\nSELECT thread#, group#, bytes/1024/1024 size_mb FROM v\\$standby_log ORDER BY thread#, group#;\nSQL\"",
        f"sudo -iu oracle {DB_HOME}/bin/orapwd file={DB_HOME}/dbs/orapw{primary_unique} force=y format=12 password=\"$DG_PASSWORD\"",
        f"mkdir -p {GRID_BASE}/dbs",
        f"cp {DB_HOME}/dbs/orapw{primary_unique} {GRID_BASE}/dbs/orapw{primary_unique}",
        f"chown grid:oinstall {GRID_BASE}/dbs/orapw{primary_unique}",
        f"chmod 600 {GRID_BASE}/dbs/orapw{primary_unique}",
        "echo 'Password file baseline created from target environment secret.'",
    ]
    return shell_script("Configure primary for Active Data Guard", lines)


def _duplicate_standby_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_unique = config.primary_site.db_unique_name
    standby_unique = standby.db_unique_name
    standby_sid = _instance_name(standby, 0, config.install_type)
    lines = [
        _dg_secret_export(config),
        f"if sudo -iu oracle bash -lc \"export ORACLE_SID={standby_sid}; sqlplus -s / as sysdba <<'SQL' >/tmp/oracle-auto-{standby_unique}-role.out 2>/tmp/oracle-auto-{standby_unique}-role.err\nSET HEADING OFF FEEDBACK OFF PAGESIZE 0\nSELECT database_role FROM v\\$database;\nSQL\" && grep -qi 'PHYSICAL STANDBY' /tmp/oracle-auto-{standby_unique}-role.out; then",
        f"  echo 'Standby database {standby_unique} already duplicated; skipping RMAN duplicate.'",
        "else",
        f"  sudo -iu oracle env ORACLE_HOME={DB_HOME} TNS_ADMIN={DB_HOME}/network/admin {DB_HOME}/bin/tnsping {primary_unique}",
        f"  sudo -iu oracle env ORACLE_HOME={DB_HOME} TNS_ADMIN={DB_HOME}/network/admin {DB_HOME}/bin/tnsping {standby_unique}",
        f"  sudo -iu oracle bash -lc \"export ORACLE_SID={standby_sid}; export TNS_ADMIN={DB_HOME}/network/admin; {DB_HOME}/bin/rman target sys/\\\"$DG_PASSWORD\\\"@{primary_unique} auxiliary sys/\\\"$DG_PASSWORD\\\"@{standby_unique} <<'RMAN'\nDUPLICATE TARGET DATABASE FOR STANDBY FROM ACTIVE DATABASE DORECOVER NOFILENAMECHECK;\nRMAN\"",
        f"  sudo -iu oracle bash -lc \"export ORACLE_SID={standby_sid}; sqlplus -s / as sysdba <<'SQL'\nWHENEVER SQLERROR CONTINUE\nSHUTDOWN IMMEDIATE;\nSQL\"",
        "fi",
        f"sudo -iu oracle {DB_HOME}/bin/srvctl start database -db {standby_unique} -startoption MOUNT || true",
    ]
    return shell_script("Duplicate standby from active primary", lines)


def _start_recovery_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    standby_unique = standby.db_unique_name
    standby_sid = _instance_name(standby, 0, config.install_type)
    lines = [
        f"sudo -iu oracle {DB_HOME}/bin/srvctl start database -db {standby_unique} -startoption MOUNT || true",
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={standby_sid}; sqlplus -s / as sysdba <<'SQL'\nWHENEVER SQLERROR CONTINUE\nALTER DATABASE RECOVER MANAGED STANDBY DATABASE CANCEL;\nALTER DATABASE RECOVER MANAGED STANDBY DATABASE DISCONNECT FROM SESSION;\nSELECT name, open_mode, database_role FROM v\\$database;\nSELECT process, status, thread#, sequence# FROM v\\$managed_standby ORDER BY process;\nSQL\"",
    ]
    return shell_script("Start Active Data Guard recovery", lines)


def _verify_primary_dataguard_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_sid = _instance_name(config.primary_site, 0, config.install_type)
    lines = [
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={primary_sid}; sqlplus -s / as sysdba <<'SQL'\nWHENEVER SQLERROR EXIT SQL.SQLCODE\nALTER SYSTEM ARCHIVE LOG CURRENT;\nALTER SYSTEM SWITCH LOGFILE;\nSELECT name, open_mode, database_role, protection_mode FROM v\\$database;\nSELECT dest_id, status, target, destination, error FROM v\\$archive_dest_status WHERE target = 'STANDBY' OR dest_id <= 2 ORDER BY dest_id;\nSQL\"",
    ]
    return shell_script("Verify primary Data Guard transport", lines)


def _verify_standby_dataguard_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    standby_unique = standby.db_unique_name
    standby_sid = _instance_name(standby, 0, config.install_type)
    lines = [
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={standby_sid}; sqlplus -s / as sysdba <<'SQL'\nWHENEVER SQLERROR CONTINUE\nALTER DATABASE RECOVER MANAGED STANDBY DATABASE CANCEL;\nALTER DATABASE OPEN READ ONLY;\nALTER DATABASE RECOVER MANAGED STANDBY DATABASE DISCONNECT FROM SESSION;\nSELECT name, open_mode, database_role FROM v\\$database;\nSELECT name, value, unit FROM v\\$dataguard_stats;\nSELECT process, status, thread#, sequence# FROM v\\$managed_standby ORDER BY process;\nSQL\"",
        f"alert_log=$(sudo -iu oracle bash -lc \"ls -1t {ORACLE_BASE}/diag/rdbms/*/*/trace/alert_*.log 2>/dev/null | head -1\") || true",
        "if test -n \"${alert_log:-}\"; then sudo -iu oracle tail -n 80 \"$alert_log\"; fi",
    ]
    return shell_script("Verify standby Data Guard apply", lines)


def _broker_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    primary_unique = config.primary_site.db_unique_name
    standby_unique = standby.db_unique_name
    primary_sid = _instance_name(config.primary_site, 0, config.install_type)
    broker_name = f"{primary_unique}_{standby_unique}_BROKER"
    primary_static = _static_connect_identifier(primary_unique, _dataguard_endpoint(config, config.primary_site, final=True))
    standby_static = _static_connect_identifier(standby_unique, _dataguard_endpoint(config, standby, final=True))
    lines = [
        _dg_secret_export(config),
        f"sudo -iu oracle env ORACLE_HOME={DB_HOME} TNS_ADMIN={DB_HOME}/network/admin {DB_HOME}/bin/tnsping {primary_unique}",
        f"sudo -iu oracle env ORACLE_HOME={DB_HOME} TNS_ADMIN={DB_HOME}/network/admin {DB_HOME}/bin/tnsping {standby_unique}",
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={primary_sid}; sqlplus -s / as sysdba <<'SQL'\nWHENEVER SQLERROR EXIT SQL.SQLCODE\nSELECT name, open_mode, database_role FROM v\\$database;\nALTER SYSTEM SET DG_BROKER_START=TRUE SCOPE=BOTH SID='*';\nSQL\"",
        f"sudo -iu oracle bash -lc \"export TNS_ADMIN={DB_HOME}/network/admin; sqlplus -L -s sys/\\\"$DG_PASSWORD\\\"@{standby_unique} as sysdba <<'SQL'\nWHENEVER SQLERROR EXIT SQL.SQLCODE\nSELECT name, open_mode, database_role FROM v\\$database;\nALTER SYSTEM SET DG_BROKER_START=TRUE SCOPE=BOTH SID='*';\nSQL\"",
        "set +e",
        f"broker_output=$(sudo -iu oracle env TNS_ADMIN={DB_HOME}/network/admin dgmgrl / <<'DGMGRL'\nSHOW CONFIGURATION;\nDGMGRL\n)",
        "broker_rc=$?",
        "set -e",
        "if test \"$broker_rc\" -eq 0 && printf '%s\\n' \"$broker_output\" | grep -qi 'Configuration -'; then",
        "  echo 'Data Guard Broker configuration already exists; validating existing configuration.'",
        "  printf '%s\\n' \"$broker_output\"",
        f"  sudo -iu oracle env TNS_ADMIN={DB_HOME}/network/admin dgmgrl / <<'DGMGRL'\nEDIT DATABASE '{primary_unique}' SET PROPERTY StaticConnectIdentifier = '{primary_static}';\nEDIT DATABASE '{standby_unique}' SET PROPERTY StaticConnectIdentifier = '{standby_static}';\nVALIDATE DATABASE '{primary_unique}';\nVALIDATE DATABASE '{standby_unique}';\nSHOW DATABASE VERBOSE '{primary_unique}';\nSHOW DATABASE VERBOSE '{standby_unique}';\nSHOW CONFIGURATION VERBOSE;\nDGMGRL",
        "else",
        f"  sudo -iu oracle env TNS_ADMIN={DB_HOME}/network/admin dgmgrl / <<'DGMGRL'\nCREATE CONFIGURATION '{broker_name}' AS PRIMARY DATABASE IS '{primary_unique}' CONNECT IDENTIFIER IS {primary_unique};\nADD DATABASE '{standby_unique}' AS CONNECT IDENTIFIER IS {standby_unique} MAINTAINED AS PHYSICAL;\nEDIT DATABASE '{primary_unique}' SET PROPERTY StaticConnectIdentifier = '{primary_static}';\nEDIT DATABASE '{standby_unique}' SET PROPERTY StaticConnectIdentifier = '{standby_static}';\nENABLE CONFIGURATION;\nVALIDATE DATABASE '{primary_unique}';\nVALIDATE DATABASE '{standby_unique}';\nSHOW DATABASE VERBOSE '{primary_unique}';\nSHOW DATABASE VERBOSE '{standby_unique}';\nSHOW CONFIGURATION VERBOSE;\nDGMGRL",
        "fi",
    ]
    return shell_script("Configure Data Guard Broker", lines)


def _dg_secret_export(config: AutomationConfig) -> str:
    return (
        f'DG_PASSWORD="${{{config.secrets.dg_password_env}:?Set {config.secrets.dg_password_env} on target before running Data Guard steps}}"\n'
        "export DG_PASSWORD"
    )


def _tnsnames_content(primary_unique: str, primary_host: str, standby_unique: str, standby_host: str) -> str:
    return f"""{primary_unique} =
  (DESCRIPTION =
    (ADDRESS = (PROTOCOL = TCP)(HOST = {primary_host})(PORT = 1521))
    (CONNECT_DATA =
      (SERVER = DEDICATED)
      (SERVICE_NAME = {primary_unique})
    )
  )

{standby_unique} =
  (DESCRIPTION =
    (ADDRESS = (PROTOCOL = TCP)(HOST = {standby_host})(PORT = 1521))
    (CONNECT_DATA =
      (SERVER = DEDICATED)
      (SERVICE_NAME = {standby_unique})
    )
  )"""


def _dataguard_endpoint(config: AutomationConfig, site, *, final: bool) -> str:
    if final and config.install_type == "rac" and site.scan_name:
        return site.scan_name
    return _dataguard_duplicate_endpoint(config, site)


def _dataguard_duplicate_endpoint(config: AutomationConfig, site) -> str:
    node = site.nodes[0]
    return node.public_ip or node.host


def _instance_name(site, index: int, install_type: str) -> str:
    if install_type == "rac":
        return f"{site.db_unique_name}{index + 1}"
    return site.db_unique_name


def _srvctl_instance_lines(site, db_unique_name: str, install_type: str) -> list[str]:
    if install_type != "rac":
        return []
    lines: list[str] = []
    for index, node in enumerate(site.nodes):
        instance_name = _instance_name(site, index, install_type)
        lines.extend([
            f"if sudo -iu oracle {DB_HOME}/bin/srvctl config database -db {db_unique_name} | grep -qw {instance_name}; then",
            f"  echo 'RAC instance {instance_name} already registered for {db_unique_name}.'",
            "else",
            f"  sudo -iu oracle {DB_HOME}/bin/srvctl add instance -db {db_unique_name} -instance {instance_name} -node {node.host}",
            "fi",
        ])
    return lines


def _static_connect_identifier(db_unique_name: str, host: str) -> str:
    return (
        f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST={host})(PORT=1521))"
        f"(CONNECT_DATA=(SERVICE_NAME={db_unique_name})(SERVER=DEDICATED)))"
    )


def _listener_sid_content(local_unique: str, local_sid: str) -> str:
    return f"""# BEGIN ORACLE-AUTO DATAGUARD
SID_LIST_LISTENER =
  (SID_LIST =
    (SID_DESC =
      (GLOBAL_DBNAME = {local_unique})
      (ORACLE_HOME = {DB_HOME})
      (SID_NAME = {local_sid})
    )
  )
# END ORACLE-AUTO DATAGUARD"""


def _listener_address_content(local_host: str) -> str:
    return f"""# BEGIN ORACLE-AUTO DATAGUARD LISTENER ADDRESS
LISTENER =
  (DESCRIPTION_LIST =
    (DESCRIPTION =
      (ADDRESS = (PROTOCOL = TCP)(HOST = {local_host})(PORT = 1521))
    )
  )
# END ORACLE-AUTO DATAGUARD LISTENER ADDRESS"""


def _standby_pfile_content(primary_db_name: str, standby_unique: str, standby_host: str) -> str:
    return f"""*.db_name='{primary_db_name}'
*.db_unique_name='{standby_unique}'
*.compatible='19.0.0'
*.remote_login_passwordfile='EXCLUSIVE'
*.db_create_file_dest='+DATA'
*.db_recovery_file_dest='+RECO'
*.db_recovery_file_dest_size='50G'
*.diagnostic_dest='{ORACLE_BASE}'
*.audit_file_dest='{ORACLE_BASE}/admin/{standby_unique}/adump'
*.local_listener='(ADDRESS=(PROTOCOL=TCP)(HOST={standby_host})(PORT=1521))'
*.standby_file_management='AUTO'"""
