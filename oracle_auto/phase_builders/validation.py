"""Deployment validation phase manual.

Builds post-install checks for Clusterware, ASM, database role/open mode, and
Data Guard lag metrics.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig
from oracle_auto.phase_builders.common import DB_HOME, GRID_BASE, ORACLE_BASE, make_step


def validate_deployment_steps(config: AutomationConfig) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for node in config.all_nodes:
        steps.append(
            make_step(
                "validate-deployment",
                "validate_grid_asm",
                node,
                "Validate Grid Infrastructure and ASM",
                _validate_grid_asm_script(config),
                timeout=600,
                warn_only=True,
                remote_marker=False,
                force_rerun=True,
            )
        )
    steps.append(
        make_step(
            "validate-deployment",
            "validate_primary_database",
            config.primary_site.nodes[0],
            "Validate primary database role, open mode, listener, and Data Guard transport",
            _validate_primary_database_script(config),
            timeout=900,
            remote_marker=False,
            force_rerun=True,
        )
    )
    if config.standby_site:
        steps.append(
            make_step(
                "validate-deployment",
                "validate_standby_database",
                config.standby_site.nodes[0],
                "Validate standby database role, open mode, listener, apply, and Data Guard gap",
                _validate_standby_database_script(config),
                timeout=900,
                remote_marker=False,
                force_rerun=True,
            )
        )
    return steps


def _validate_grid_asm_script(config: AutomationConfig) -> str:
    crs_check = "crs" if config.install_type == "rac" else "has"
    lines = [
        f"sudo -iu grid {GRID_BASE}/bin/crsctl check {crs_check}",
        f"sudo -iu grid {GRID_BASE}/bin/crsctl stat res -t",
        "sudo -iu grid asmcmd dsget",
        "sudo -iu grid asmcmd lsdg",
    ]
    return shell_script("Validate Grid and ASM", lines)


def _validate_primary_database_script(config: AutomationConfig) -> str:
    primary_unique = config.primary_site.db_unique_name
    primary_sql = """WHENEVER SQLERROR EXIT SQL.SQLCODE
PROMPT === PRIMARY DATABASE IDENTITY AND ROLE ===
SELECT name, db_unique_name, open_mode, database_role, switchover_status FROM v$database;

PROMPT === PRIMARY OPERATIONAL READINESS ===
SELECT CASE
         WHEN database_role = 'PRIMARY' AND open_mode = 'READ WRITE'
         THEN 'PASS: primary database is open read/write for application workload'
         ELSE 'FAIL: primary database is not open read/write as PRIMARY'
       END AS readiness
  FROM v$database;
"""
    if config.standby_site:
        primary_sql += """
PROMPT === PRIMARY DATA GUARD TRANSPORT DESTINATIONS ===
SELECT dest_id, status, type, database_mode, recovery_mode, destination, error
  FROM v$archive_dest_status
 WHERE dest_id <= 2 OR destination IS NOT NULL
 ORDER BY dest_id;

PROMPT === PRIMARY DATA GUARD TRANSPORT READINESS ===
SELECT CASE
         WHEN EXISTS (
           SELECT 1
             FROM v$archive_dest_status
            WHERE type = 'PHYSICAL'
              AND status = 'VALID'
              AND error IS NULL
         )
         THEN 'PASS: primary has a valid physical standby archive destination'
         ELSE 'WARN: no VALID physical standby archive destination is visible on primary'
       END AS readiness
  FROM dual;

PROMPT === PRIMARY SWITCHOVER READINESS ===
SELECT CASE
         WHEN switchover_status IN ('TO STANDBY', 'SESSIONS ACTIVE')
         THEN 'PASS: primary switchover status is compatible with planned switchover'
         ELSE 'WARN: review primary switchover status before planned switchover: ' || switchover_status
       END AS readiness
  FROM v$database;

PROMPT === PRIMARY ARCHIVE LOG MODE ===
ARCHIVE LOG LIST;"""
    lines = [
        f"sudo -iu oracle {DB_HOME}/bin/srvctl status database -db {primary_unique} || true",
        *_listener_ready_lines(primary_unique),
        _oracle_sqlplus(primary_unique, primary_sql),
    ]
    return shell_script("Validate primary database", lines)


def _validate_standby_database_script(config: AutomationConfig) -> str:
    standby = config.standby_site
    assert standby is not None
    standby_unique = standby.db_unique_name
    standby_sql = """WHENEVER SQLERROR EXIT SQL.SQLCODE
PROMPT === STANDBY DATABASE IDENTITY AND ROLE ===
SELECT name, db_unique_name, open_mode, database_role, switchover_status FROM v$database;

PROMPT === STANDBY OPERATIONAL READINESS ===
SELECT CASE
         WHEN database_role = 'PHYSICAL STANDBY'
          AND open_mode IN ('READ ONLY WITH APPLY', 'READ ONLY', 'MOUNTED')
         THEN 'PASS: standby database role/open mode is valid for Data Guard operations'
         ELSE 'FAIL: standby database is not in an expected physical standby open mode'
       END AS readiness
  FROM v$database;

PROMPT === STANDBY DATA GUARD LAG ===
SELECT name, value, unit FROM v$dataguard_stats;

PROMPT === STANDBY ARCHIVE GAP ===
SELECT * FROM v$archive_gap;

PROMPT === STANDBY ARCHIVE GAP READINESS ===
SELECT CASE
         WHEN COUNT(*) = 0
         THEN 'PASS: no archive gap reported by standby'
         ELSE 'WARN: archive gap rows exist; review v$archive_gap output'
       END AS readiness
  FROM v$archive_gap;

PROMPT === STANDBY RECEIVED REDO SEQUENCE ===
SELECT thread#, MAX(sequence#) last_received_sequence
  FROM v$archived_log
 GROUP BY thread#
 ORDER BY thread#;

PROMPT === STANDBY APPLIED REDO SEQUENCE ===
SELECT thread#, MAX(sequence#) last_applied_sequence
  FROM v$archived_log
 WHERE applied = 'YES'
 GROUP BY thread#
 ORDER BY thread#;

PROMPT === STANDBY MANAGED RECOVERY PROCESSES ===
SELECT process, status, thread#, sequence# FROM v$managed_standby ORDER BY process;

PROMPT === STANDBY APPLY READINESS ===
SELECT CASE
         WHEN EXISTS (
           SELECT 1
             FROM v$managed_standby
            WHERE process LIKE 'MRP%'
              AND status IN ('APPLYING_LOG', 'WAIT_FOR_LOG', 'IDLE')
         )
         THEN 'PASS: managed recovery process is active or waiting for next log'
         ELSE 'WARN: managed recovery process is not visible; review v$managed_standby'
       END AS readiness
  FROM dual;

PROMPT === STANDBY SWITCHOVER READINESS ===
SELECT CASE
         WHEN switchover_status IN ('TO PRIMARY', 'SESSIONS ACTIVE', 'NOT ALLOWED')
         THEN 'INFO: standby switchover status is ' || switchover_status || '; verify with broker/manual checks before planned switchover'
         ELSE 'WARN: review standby switchover status before planned switchover: ' || switchover_status
       END AS readiness
  FROM v$database;"""
    lines = [
        f"sudo -iu oracle {DB_HOME}/bin/srvctl status database -db {standby_unique} || true",
        *_listener_ready_lines(standby_unique),
        _oracle_sqlplus(standby_unique, standby_sql),
    ]
    return shell_script("Validate standby database", lines)


def _listener_ready_lines(service_name: str) -> list[str]:
    quoted_service = shlex.quote(service_name)
    return [
        f"sudo -iu grid env ORACLE_HOME={GRID_BASE} TNS_ADMIN={GRID_BASE}/network/admin {GRID_BASE}/bin/lsnrctl status LISTENER",
        f"sudo -iu grid env ORACLE_HOME={GRID_BASE} TNS_ADMIN={GRID_BASE}/network/admin {GRID_BASE}/bin/lsnrctl status LISTENER > /tmp/oracle-auto-listener-{service_name}.status",
        f"echo '=== LISTENER READINESS FOR {service_name} ==='",
        f"awk -v service={quoted_service} '\n"
        "  BEGIN { in_service = 0; ready = 0 }\n"
        "  index($0, \"Service \\\"\" service \"\\\"\") { in_service = 1; next }\n"
        "  in_service && $0 ~ /^Service / { in_service = 0 }\n"
        "  in_service && $0 ~ /status READY/ { ready = 1 }\n"
        "  END {\n"
        "    if (ready) {\n"
        "      print \"PASS: listener service \" service \" has READY handler\"\n"
        "      exit 0\n"
        "    }\n"
        "    print \"FAIL: listener service \" service \" does not have a READY handler\"\n"
        "    exit 1\n"
        "  }\n"
        f"' /tmp/oracle-auto-listener-{service_name}.status",
    ]


def _oracle_sqlplus(sid: str, sql: str) -> str:
    return (
        f"export ORACLE_SID={shlex.quote(sid)}\n"
        f"sudo -iu oracle env ORACLE_HOME={DB_HOME} ORACLE_BASE={ORACLE_BASE} ORACLE_SID=\"$ORACLE_SID\" "
        f"PATH={DB_HOME}/bin:/usr/local/bin:/usr/bin:/bin LD_LIBRARY_PATH={DB_HOME}/lib "
        f"{DB_HOME}/bin/sqlplus -s / as sysdba <<'SQL'\n"
        "SET LINESIZE 220 PAGESIZE 100 TRIMSPOOL ON TAB OFF\n"
        "COLUMN readiness FORMAT A100\n"
        "COLUMN name FORMAT A12\n"
        "COLUMN db_unique_name FORMAT A20\n"
        "COLUMN open_mode FORMAT A24\n"
        "COLUMN database_role FORMAT A20\n"
        "COLUMN switchover_status FORMAT A24\n"
        "COLUMN value FORMAT A32\n"
        "COLUMN unit FORMAT A32\n"
        "COLUMN destination FORMAT A40\n"
        "COLUMN error FORMAT A60\n"
        f"{sql}\n"
        "SQL"
    )
