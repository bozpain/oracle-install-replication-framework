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
            )
        )
    steps.append(
        make_step(
            "validate-deployment",
            "validate_database",
            config.primary_site.nodes[0],
            "Validate database role, open mode, services, and Data Guard lag",
            _validate_database_script(config),
            timeout=900,
        )
    )
    return steps


def _validate_grid_asm_script(config: AutomationConfig) -> str:
    crs_check = "crs" if config.install_type == "rac" else "has"
    lines = [
        f"sudo -iu grid {GRID_BASE}/bin/crsctl check {crs_check}",
        f"sudo -iu grid {GRID_BASE}/bin/crsctl stat res -t",
        "sudo -iu grid asmcmd lsdg",
    ]
    return shell_script("Validate Grid and ASM", lines)


def _validate_database_script(config: AutomationConfig) -> str:
    primary_unique = config.primary_site.db_unique_name
    dg_sql = ""
    if config.standby_site:
        dg_sql = "SELECT name, value, unit FROM v$dataguard_stats;"
    validation_sql = f"""WHENEVER SQLERROR EXIT SQL.SQLCODE
SELECT name, open_mode, database_role FROM v$database;
{dg_sql}"""
    lines = [
        f"sudo -iu oracle {DB_HOME}/bin/srvctl status database -db {primary_unique} || true",
        _oracle_sqlplus(primary_unique, validation_sql),
    ]
    return shell_script("Validate database", lines)


def _oracle_sqlplus(sid: str, sql: str) -> str:
    return (
        f"export ORACLE_SID={shlex.quote(sid)}\n"
        f"sudo -iu oracle env ORACLE_HOME={DB_HOME} ORACLE_BASE={ORACLE_BASE} ORACLE_SID=\"$ORACLE_SID\" "
        f"PATH={DB_HOME}/bin:/usr/local/bin:/usr/bin:/bin LD_LIBRARY_PATH={DB_HOME}/lib "
        f"{DB_HOME}/bin/sqlplus -s / as sysdba <<'SQL'\n"
        f"{sql}\n"
        "SQL"
    )
