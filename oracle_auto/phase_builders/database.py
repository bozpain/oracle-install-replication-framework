"""Database software and primary creation phase manual.

Owns DB home installation and DBCA silent database creation. Data Guard-specific
database actions live in `dataguard.py`.
"""

from __future__ import annotations

import re
import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig, SiteConfig
from oracle_auto.phase_builders.common import DB_HOME, STAGE, ensure_swap_lines, make_step, stage_patch_lines
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
        *ensure_swap_lines(),
        f"test -x {DB_HOME}/runInstaller || sudo -iu oracle unzip -oq {shlex.quote(config.installer.sources_path)}/{shlex.quote(config.installer.db_zip)} -d {DB_HOME}",
        *_db_patch_stage_lines(config),
        f"cat > {STAGE}/responses/dbhome-{site.name}.rsp <<'EOF'\n{response}\nEOF",
        f"chown oracle:oinstall {STAGE}/responses/dbhome-{site.name}.rsp",
        f"mkdir -p {STAGE}/logs",
        f"DB_INSTALL_LOG={STAGE}/logs/dbInstall-{site.name}.out",
        "echo 'Running Database software setup with RU apply when configured.'",
        "set +e",
        f"sudo -iu oracle env CV_ASSUME_DISTID=OL7 {DB_HOME}/runInstaller -silent -waitforcompletion -responseFile {STAGE}/responses/dbhome-{site.name}.rsp{_db_patch_arg(config)} -ignorePrereqFailure 2>&1 | tee \"$DB_INSTALL_LOG\"",
        "db_install_rc=${PIPESTATUS[0]}",
        "set -e",
        "if test \"$db_install_rc\" -ne 0; then",
        "  echo \"ERROR: Database software setup failed. See $DB_INSTALL_LOG\" >&2",
        "  grep -HniE 'SEVERE|ERROR|FATAL|INS-|OPATCH|applyRU|failed|failure' \"$DB_INSTALL_LOG\" || true",
        "  exit \"$db_install_rc\"",
        "fi",
        *_db_ru_validation_lines(config),
    ]
    return shell_script(f"Install Database home for {site.name}", lines)


def _db_patch_stage_lines(config: AutomationConfig) -> list[str]:
    if config.installer.db_patch is None:
        return []
    return stage_patch_lines(config.installer.sources_path, config.installer.db_patch.file, "DB_PATCH_TOP")


def _db_patch_arg(config: AutomationConfig) -> str:
    if config.installer.db_patch is None:
        return ""
    return ' -applyRU "$DB_PATCH_TOP"'


def _db_patch_id_regex(config: AutomationConfig) -> str | None:
    if config.installer.db_patch is None:
        return None
    patch_id = config.installer.db_patch.patch_id
    if patch_id:
        return re.escape(str(patch_id))
    file_name = str(config.installer.db_patch.file).split("/")[-1]
    match = re.match(r"p?(\d{5,})(?:_|$)", file_name)
    if match:
        return re.escape(match.group(1))
    return None


def _db_ru_validation_lines(config: AutomationConfig) -> list[str]:
    if config.installer.db_patch is None:
        return [
            "echo 'No Database RU configured; skipping RU validation.'",
        ]

    patch_id = _db_patch_id_regex(config)
    if patch_id is None:
        return [
            "echo 'ERROR: Database RU patch id cannot be derived from patch filename. Set installer.db_patch.patch_id to the numeric OPatch patch id shown by lspatches.' >&2",
            "exit 1",
        ]
    return [
        "echo 'Validating Database RU patch inventory before root script.'",
        f"sudo -iu oracle {DB_HOME}/OPatch/opatch lspatches",
        f"if ! sudo -iu oracle {DB_HOME}/OPatch/opatch lspatches | grep -Eq '^({patch_id});'; then",
        f"  echo 'ERROR: Database RU patch id not found in OPatch inventory after applyRU. Expected regex: ^({patch_id});' >&2",
        f"  sudo -iu oracle {DB_HOME}/OPatch/opatch lsinventory || true",
        "  exit 1",
        "fi",
        f"sudo -iu oracle {DB_HOME}/bin/oraversion -version || true",
        f"if sudo -iu oracle {DB_HOME}/bin/oraversion -version 2>/dev/null | grep -q '19.3.0.0.0'; then",
        "  echo 'ERROR: Database home still reports 19.3.0.0.0 after RU apply. Refusing to continue.' >&2",
        "  exit 1",
        "fi",
        "echo 'Database RU validation passed.'",
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
        f"sudo -iu oracle {DB_HOME}/bin/dbca -silent -createDatabase -responseFile {STAGE}/responses/dbca-primary.rsp",
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
