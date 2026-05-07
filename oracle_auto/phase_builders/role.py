"""Role operation phase manual.

Builds switchover and failover scripts. Failover remains guarded in the CLI by
the `--yes` flag for non-dry-run execution.
"""

from __future__ import annotations

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig
from oracle_auto.phase_builders.common import make_step


def switchover_steps(config: AutomationConfig) -> list[AutomationStep]:
    if not config.standby_site:
        return []
    return [
        make_step(
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
        make_step(
            "failover",
            "failover_to_standby",
            config.standby_site.nodes[0],
            "Failover to standby database",
            _failover_script(config),
            timeout=3600,
        )
    ]


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

