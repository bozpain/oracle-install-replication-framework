"""Database software and primary creation phase manual.

Owns DB home installation and DBCA silent database creation. Data Guard-specific
database actions live in `dataguard.py`.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig, SiteConfig
from oracle_auto.phase_builders.common import (
    DB_HOME,
    INVENTORY_LOCATION,
    STAGE,
    ensure_swap_lines,
    make_step,
    oracle_home_inventory_pointer_lines,
    oracle_user_group_lines,
    stage_patch_lines,
)
from oracle_auto.response_files.database import db_home_response, dbca_response


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
    response = db_home_response()
    lines = [
        f"mkdir -p {STAGE}/responses",
        f"mkdir -p {STAGE}/logs",
        f"DB_INSTALL_LOG={STAGE}/logs/dbInstall-{site.name}.out",
        *ensure_swap_lines(),
        *oracle_user_group_lines(),
        *_fresh_db_home_lines(config, site),
        f"test -x {DB_HOME}/runInstaller || sudo -iu oracle unzip -oq {shlex.quote(config.installer.sources_path)}/{shlex.quote(config.installer.db_zip)} -d {DB_HOME}",
        *oracle_home_inventory_pointer_lines(DB_HOME, "oracle"),
        *_db_opatch_lines(config),
        *_db_patch_stage_lines(config),
        f"cat > {STAGE}/responses/dbhome-{site.name}.rsp <<'EOF'\n{response}\nEOF",
        f"chown oracle:oinstall {STAGE}/responses/dbhome-{site.name}.rsp",
        "DB_SOFTWARE_READY=false",
        "DB_HOME_INVENTORY_REGISTERED=false",
        f"if test -r {INVENTORY_LOCATION}/ContentsXML/inventory.xml && grep -Fq 'LOC=\"{DB_HOME}\"' {INVENTORY_LOCATION}/ContentsXML/inventory.xml; then DB_HOME_INVENTORY_REGISTERED=true; fi",
        "DB_PREVIOUS_INSTALL_FAILED=false",
        "if test -f \"$DB_INSTALL_LOG\" && grep -Eq 'FATAL|ERROR|INS-|failed|failure' \"$DB_INSTALL_LOG\"; then DB_PREVIOUS_INSTALL_FAILED=true; fi",
        f"if test -x {DB_HOME}/bin/oraversion; then",
        f"  db_version=$(sudo -iu oracle {DB_HOME}/bin/oraversion -compositeVersion 2>/dev/null || sudo -iu oracle {DB_HOME}/bin/oraversion -version 2>/dev/null || true)",
        "  if test -n \"$db_version\"; then",
        "    echo \"Existing Database Oracle version: $db_version\"",
        *_db_existing_version_ready_lines(config),
        "  fi",
        "fi",
        "if test \"$DB_SOFTWARE_READY\" = true; then",
        "  echo 'Database software already installed with expected version; skipping runInstaller.'",
        "else",
        "  echo 'Running Database software setup with RU apply when configured.'",
        "  set +e",
        f"  sudo -iu oracle env CV_ASSUME_DISTID=OL7 ORACLE_HOME={DB_HOME} ORACLE_BASE=/u01/app/oracle {DB_HOME}/runInstaller -silent -waitforcompletion -responseFile {STAGE}/responses/dbhome-{site.name}.rsp{_db_patch_arg(config)} -ignorePrereqFailure 2>&1 | tee \"$DB_INSTALL_LOG\"",
        "  db_install_rc=${PIPESTATUS[0]}",
        "  set -e",
        "  if test \"$db_install_rc\" -ne 0; then",
        "    echo \"ERROR: Database software setup failed. See $DB_INSTALL_LOG\" >&2",
        "    grep -HniE 'SEVERE|ERROR|FATAL|INS-|OPATCH|applyRU|failed|failure' \"$DB_INSTALL_LOG\" || true",
        f"    find {DB_HOME}/cfgtoollogs/opatchauto -type f -name '*.log' -printf '%T@ %p\\n' 2>/dev/null | sort -nr | head -3 | cut -d' ' -f2- | while read -r log_file; do echo \"--- Recent OPatch log: $log_file\" >&2; grep -HniE 'SEVERE|ERROR|FATAL|failed|failure|conflict|prereq' \"$log_file\" | tail -20 >&2 || true; done",
        "    exit \"$db_install_rc\"",
        "  fi",
        f"  mkdir -p {DB_HOME}/install",
        f"  touch {DB_HOME}/install/oracle_auto_db_software_installed.marker",
        "fi",
        *_db_ru_validation_lines(config),
    ]
    return shell_script(f"Install Database home for {site.name}", lines)


def _fresh_db_home_lines(config: AutomationConfig, site: SiteConfig) -> list[str]:
    if config.installer.db_patch is None:
        return []
    db_zip = f"{config.installer.sources_path}/{config.installer.db_zip}"
    unique = site.db_unique_name
    return [
        "DB_HOME_DIRTY=false",
        "DB_HOME_INVENTORY_REGISTERED=false",
        f"if test -r {INVENTORY_LOCATION}/ContentsXML/inventory.xml && grep -Fq 'LOC=\"{DB_HOME}\"' {INVENTORY_LOCATION}/ContentsXML/inventory.xml; then DB_HOME_INVENTORY_REGISTERED=true; fi",
        "DB_PREVIOUS_INSTALL_FAILED=false",
        "if test -f \"$DB_INSTALL_LOG\" && grep -Eq 'FATAL|ERROR|INS-|failed|failure' \"$DB_INSTALL_LOG\"; then DB_PREVIOUS_INSTALL_FAILED=true; fi",
        f"if test -x {DB_HOME}/bin/oraversion; then",
        f"  db_home_version=$(sudo -iu oracle {DB_HOME}/bin/oraversion -compositeVersion 2>/dev/null || sudo -iu oracle {DB_HOME}/bin/oraversion -version 2>/dev/null || true)",
        "  case \"$db_home_version\" in",
        "    *19.3.0.0.0*) DB_HOME_DIRTY=true ;;",
        "    *) if test \"$DB_PREVIOUS_INSTALL_FAILED\" = true || test \"$DB_HOME_INVENTORY_REGISTERED\" != true; then DB_HOME_DIRTY=true; fi ;;",
        "  esac",
        f"elif test -x {DB_HOME}/runInstaller; then",
        "  DB_HOME_DIRTY=true",
        "fi",
        "if test \"$DB_HOME_DIRTY\" = true; then",
        f"  if test -x {DB_HOME}/bin/srvctl && sudo -iu oracle {DB_HOME}/bin/srvctl config database -db {unique} >/dev/null 2>&1; then",
        f"    echo 'ERROR: Database home looks unpatched/dirty, but database {unique} is already registered. Refusing to clean DB home automatically.' >&2",
        "    exit 1",
        "  fi",
        "  echo 'Database home is unpatched, partially installed, or failed a previous installer run; resetting DB home before RU install.'",
        f"  if test -x {DB_HOME}/runInstaller; then sudo -iu oracle env ORACLE_HOME={DB_HOME} ORACLE_BASE=/u01/app/oracle {DB_HOME}/runInstaller -silent -detachHome ORACLE_HOME={DB_HOME} >/dev/null 2>&1 || true; fi",
        f"  find {DB_HOME} -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} +",
        f"  sudo -iu oracle unzip -oq {shlex.quote(db_zip)} -d {DB_HOME}",
        "fi",
    ]


def _db_opatch_lines(config: AutomationConfig) -> list[str]:
    if not config.installer.opatch_zip:
        return []
    opatch_zip = f"{config.installer.sources_path}/{config.installer.opatch_zip}"
    return [
        f"test -s {shlex.quote(opatch_zip)}",
        "echo 'Updating Database OPatch before Database RU apply.'",
        f"rm -rf {DB_HOME}/OPatch",
        f"sudo -iu oracle unzip -oq {shlex.quote(opatch_zip)} -d {DB_HOME}",
        *oracle_home_inventory_pointer_lines(DB_HOME, "oracle"),
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch version",
    ]


def _db_patch_stage_lines(config: AutomationConfig) -> list[str]:
    if config.installer.db_patch is None:
        return []
    return stage_patch_lines(
        config.installer.sources_path,
        config.installer.db_patch.file,
        "DB_PATCH_TOP",
        patch_id=config.installer.db_patch.patch_id,
    )


def _db_patch_arg(config: AutomationConfig) -> str:
    if config.installer.db_patch is None:
        return ""
    return ' -applyRU "$DB_PATCH_TOP"'


def _db_existing_version_ready_lines(config: AutomationConfig) -> list[str]:
    if config.installer.db_patch is None:
        return ["    DB_SOFTWARE_READY=true"]
    return [
        "    case \"$db_version\" in",
        "      *19.3.0.0.0*) ;;",
        f"      *) if test -f {DB_HOME}/install/oracle_auto_db_software_installed.marker || (test \"$DB_HOME_INVENTORY_REGISTERED\" = true && test \"$DB_PREVIOUS_INSTALL_FAILED\" != true); then DB_SOFTWARE_READY=true; fi ;;",
        "    esac",
    ]


def _db_ru_validation_lines(config: AutomationConfig) -> list[str]:
    if config.installer.db_patch is None:
        return [
            "echo 'No Database RU configured; skipping RU validation.'",
        ]

    return [
        "echo 'Validating Database RU with oraversion before root script.'",
        f"db_version=$(sudo -iu oracle {DB_HOME}/bin/oraversion -compositeVersion 2>/dev/null || sudo -iu oracle {DB_HOME}/bin/oraversion -version 2>/dev/null || true)",
        "echo \"Database Oracle version: ${db_version:-unknown}\"",
        "if test -z \"$db_version\"; then",
        "  echo 'ERROR: Database oraversion did not return a version after applyRU. Refusing to continue.' >&2",
        "  exit 1",
        "fi",
        "case \"$db_version\" in",
        "  *19.3.0.0.0*) echo 'ERROR: Database home still reports 19.3.0.0.0 after applyRU. Refusing to continue.' >&2; exit 1 ;;",
        "esac",
        "echo 'Database RU validation passed by oraversion.'",
    ]


def _db_root_script() -> str:
    return shell_script(
        "Run Database root script",
        [
            "test -x /u01/app/oraInventory/orainstRoot.sh && /u01/app/oraInventory/orainstRoot.sh || true",
            f"test -x {DB_HOME}/root.sh",
            f"if test -f {DB_HOME}/install/root_script_ran.marker; then echo 'Database root script marker exists; skipping.'; else {DB_HOME}/root.sh && mkdir -p {DB_HOME}/install && touch {DB_HOME}/install/root_script_ran.marker; fi",
        ],
    )


def _create_database_script(config: AutomationConfig) -> str:
    db_name = config.primary_site.db_name or config.primary_site.db_unique_name
    unique = config.primary_site.db_unique_name
    response = dbca_response(config, db_name, unique)
    lines = [
        _secret_exports(config),
        f"mkdir -p {STAGE}/responses",
        "umask 077",
        f"cat > {STAGE}/responses/dbca-primary.rsp <<EOF\n{response}\nEOF",
        f"chown oracle:oinstall {STAGE}/responses/dbca-primary.rsp",
        f"chmod 600 {STAGE}/responses/dbca-primary.rsp",
        f"if sudo -iu oracle {DB_HOME}/bin/srvctl config database -db {unique} >/dev/null 2>&1; then",
        f"  echo 'Database {unique} already registered in srvctl; skipping DBCA createDatabase.'",
        "else",
        f"  sudo -iu oracle {DB_HOME}/bin/dbca -silent -createDatabase -responseFile {STAGE}/responses/dbca-primary.rsp",
        "fi",
        f"shred -u {STAGE}/responses/dbca-primary.rsp 2>/dev/null || rm -f {STAGE}/responses/dbca-primary.rsp",
        f"sudo -iu oracle {DB_HOME}/bin/srvctl status database -db {unique} || true",
        f"sudo -iu oracle bash -lc \"export ORACLE_SID={unique}; sqlplus -s / as sysdba <<'SQL'\nALTER DATABASE FORCE LOGGING;\nARCHIVE LOG LIST;\nSELECT name, open_mode, database_role FROM v\\$database;\nSQL\"",
    ]
    return shell_script("Create primary database", lines)


def _secret_exports(config: AutomationConfig) -> str:
    return (
        f'SYS_PASSWORD="${{{config.secrets.sys_password_env}:?Set {config.secrets.sys_password_env} on target before running create-database}}"\n'
        f'SYSTEM_PASSWORD="${{{config.secrets.system_password_env}:?Set {config.secrets.system_password_env} on target before running create-database}}"\n'
        f'ASMSNMP_PASSWORD="${{{config.secrets.asmsnmp_password_env}:?Set {config.secrets.asmsnmp_password_env} on target before running create-database}}"\n'
        "export SYS_PASSWORD SYSTEM_PASSWORD ASMSNMP_PASSWORD"
    )
