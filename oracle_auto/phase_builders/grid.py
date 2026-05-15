"""Grid Infrastructure phase manual.

Builds Grid Infrastructure response files and root script execution steps. RAC
and single-GI share this module because both use GI and ASM.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig, SiteConfig
from oracle_auto.phase_builders.common import GRID_BASE, GRID_BASE_DIR, STAGE, ensure_swap_lines, make_step, stage_patch_lines
from oracle_auto.phase_builders.storage import asm_discovery_string, asm_disk_spec, asm_entries
from oracle_auto.response_files.grid import grid_response


def install_grid_steps(config: AutomationConfig) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for site in config.sites:
        first = site.nodes[0]
        steps.append(
            make_step(
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
                make_step(
                    "install-grid",
                    f"root_scripts_{site.name}_{node.short_name}",
                    node,
                    f"Run Grid root scripts for {node.host}",
                    _grid_root_script(),
                    timeout=1800,
                )
            )
        steps.append(
            make_step(
                "install-grid",
                f"config_tools_{site.name}",
                first,
                f"Run Grid configuration tools for {site.name}",
                _grid_config_tools_script(config, site),
                timeout=3600,
            )
        )
    return steps


def _install_grid_script(config: AutomationConfig, site: SiteConfig) -> str:
    response = grid_response(config, site)
    lines = [
        _asm_password_export(config),
        f"mkdir -p {STAGE}/responses",
        "umask 077",
        _scan_dns_guard(site),
        _hosts_guard(config),
        *ensure_swap_lines(),
        *_fresh_grid_home_lines(config),
        *_grid_opatch_lines(config),
        *_grid_patch_stage_lines(config),
        f"cat > {STAGE}/responses/grid-{site.name}.rsp <<EOF\n{response}\nEOF",
        f"chown grid:oinstall {STAGE}/responses/grid-{site.name}.rsp",
        f"chmod 600 {STAGE}/responses/grid-{site.name}.rsp",
        f"mkdir -p {STAGE}/logs",
        f"GRID_SETUP_LOG={STAGE}/logs/gridSetup-{site.name}.out",
        "if test ! -f /etc/oracle/olr.loc && test -x "
        f"{GRID_BASE}/root.sh && ls {GRID_BASE}/install/response/grid_*.rsp >/dev/null 2>&1; then",
        "  echo 'Grid software already installed; skipping software setup and continuing with root scripts/config tools.'",
        "else",
        "set +e",
        f"sudo -iu grid env CV_ASSUME_DISTID=OL7 ORACLE_BASE={GRID_BASE_DIR} {GRID_BASE}/gridSetup.sh -silent -waitforcompletion -responseFile {STAGE}/responses/grid-{site.name}.rsp{_grid_patch_arg(config)} -ignorePrereqFailure 2>&1 | tee \"$GRID_SETUP_LOG\"",
        "grid_setup_rc=${PIPESTATUS[0]}",
        "set -e",
        "if test \"$grid_setup_rc\" -ne 0; then",
        "  if grep -Eq 'Successfully Setup Software|execute the following script|executeConfigTools' \"$GRID_SETUP_LOG\" && test -x "
        f"{GRID_BASE}/root.sh; then",
        "    echo 'Grid software setup completed; root scripts and config tools will run in following steps.'",
        "  else",
        "    exit \"$grid_setup_rc\"",
        "  fi",
        "fi",
        "fi",
    ]
    return shell_script(f"Install Grid Infrastructure for {site.name}", lines)


def _grid_patch_stage_lines(config: AutomationConfig) -> list[str]:
    if config.installer.grid_patch is None:
        return []
    return [
        *stage_patch_lines(config.installer.sources_path, config.installer.grid_patch.file, "GRID_PATCH_TOP"),
        'ls -ld "$GRID_PATCH_TOP"',
        'namei -l "$GRID_PATCH_TOP" || true',
        'sudo -iu grid test -d "$GRID_PATCH_TOP"',
        'sudo -iu grid ls -ld "$GRID_PATCH_TOP"',
    ]


def _fresh_grid_home_lines(config: AutomationConfig) -> list[str]:
    grid_zip = f"{config.installer.sources_path}/{config.installer.grid_zip}"
    return [
        "if test ! -f /etc/oracle/olr.loc && test -x "
        f"{GRID_BASE}/gridSetup.sh && ls {GRID_BASE}/install/response/grid_*.rsp >/dev/null 2>&1; then "
        "echo 'Grid software appears installed; preserving home for root scripts/config tools'; "
        "elif test ! -f /etc/oracle/olr.loc && test -x "
        f"{GRID_BASE}/gridSetup.sh; then echo 'Resetting unconfigured Grid home before install'; "
        f"find {GRID_BASE} -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} +; fi",
        f"test -x {GRID_BASE}/gridSetup.sh || sudo -iu grid unzip -oq {shlex.quote(grid_zip)} -d {GRID_BASE}",
    ]


def _grid_opatch_lines(config: AutomationConfig) -> list[str]:
    if not config.installer.opatch_zip:
        return []
    opatch_zip = f"{config.installer.sources_path}/{config.installer.opatch_zip}"
    return [
        f"test -s {shlex.quote(opatch_zip)}",
        f"rm -rf {GRID_BASE}/OPatch",
        f"sudo -iu grid unzip -oq {shlex.quote(opatch_zip)} -d {GRID_BASE}",
        f"sudo -iu grid {GRID_BASE}/OPatch/opatch version",
    ]


def _grid_patch_arg(config: AutomationConfig) -> str:
    if config.installer.grid_patch is None:
        return ""
    return ' -applyRU "$GRID_PATCH_TOP"'


def _asm_password_export(config: AutomationConfig) -> str:
    return (
        f'ASMSNMP_PASSWORD="${{{config.secrets.asmsnmp_password_env}:?'
        f'Set {config.secrets.asmsnmp_password_env} on target before running install-grid}}"\n'
        "export ASMSNMP_PASSWORD"
    )


def _grid_root_script() -> str:
    lines = [
        "test -x /u01/app/oraInventory/orainstRoot.sh && /u01/app/oraInventory/orainstRoot.sh || true",
        f"test -x {GRID_BASE}/root.sh",
        f"if sudo -iu grid {GRID_BASE}/bin/crsctl check crs >/dev/null 2>&1; then echo 'Grid appears active; skipping root.sh rerun.'; else {GRID_BASE}/root.sh; fi",
        f"sudo -iu grid {GRID_BASE}/bin/crsctl check crs || true",
        "sudo -iu grid asmcmd lsdg || true",
    ]
    return shell_script("Run Grid root scripts", lines)


def _grid_config_tools_script(config: AutomationConfig, site: SiteConfig) -> str:
    response = grid_response(config, site)
    response_file = f"{STAGE}/responses/grid-{site.name}.rsp"
    crs_check = _crs_check_command(config)
    lines = [
        _asm_password_export(config),
        f"mkdir -p {STAGE}/responses",
        f"cat > {response_file} <<EOF\n{response}\nEOF",
        f"chown grid:oinstall {response_file}",
        f"chmod 600 {response_file}",
        f"if sudo -iu grid {crs_check} >/dev/null 2>&1 && sudo -iu grid {GRID_BASE}/bin/asmcmd lsdg >/dev/null 2>&1; then",
        "  echo 'Grid configuration tools appear complete; skipping executeConfigTools.'",
        "else",
        "  echo 'Repairing unexpected root-owned Grid image files before executeConfigTools'",
        f"  find {GRID_BASE} -xdev -user root ! -perm /6000 -print | head -50 || true",
        f"  find {GRID_BASE} -xdev -user root ! -perm /6000 -exec chown grid:oinstall {{}} +",
        *_grid_known_hosts_lines(site),
        f"  mkdir -p {STAGE}/logs",
        f"  CONFIG_TOOLS_LOG={STAGE}/logs/gridConfigTools-{site.name}.out",
        "  config_tools_stamp=$(mktemp /tmp/oracle-auto-grid-config-tools.XXXXXX)",
        "  touch \"$config_tools_stamp\"",
        *_single_gi_direct_asmca_lines(config),
        f"  if sudo -iu grid {crs_check} >/dev/null 2>&1 && sudo -iu grid {GRID_BASE}/bin/asmcmd lsdg >/dev/null 2>&1; then",
        "    echo 'Grid ASM configuration complete; skipping OUI executeConfigTools replay.'",
        "  else",
        "    set +e",
        f"    sudo -iu grid env CV_ASSUME_DISTID=OL7 ORACLE_BASE={GRID_BASE_DIR} {GRID_BASE}/gridSetup.sh -executeConfigTools -responseFile {response_file} -silent 2>&1 | tee \"$CONFIG_TOOLS_LOG\"",
        "    config_tools_rc=${PIPESTATUS[0]}",
        "    set -e",
        "    if test \"$config_tools_rc\" -ne 0; then",
        "      echo 'Grid configuration tools failed; extracting recent Oracle log errors'",
        "      recent_grid_log_dirs=$(find /u01/app/oraInventory/logs /tmp -maxdepth 1 -type d -name 'GridSetupActions*' -newer \"$config_tools_stamp\" -print 2>/dev/null || true)",
        f"      for log_root in $recent_grid_log_dirs {GRID_BASE}/cfgtoollogs; do",
        "        test -n \"$log_root\" && test -d \"$log_root\" || continue",
        "        find \"$log_root\" -type f \\( -name '*.log' -o -name '*.out' -o -name '*.err' \\) -newer \"$config_tools_stamp\" -print0 2>/dev/null |",
        "          xargs -0 -r grep -HniE 'SEVERE|ERROR|FATAL|INS-|CLSRSC-|PRCR-|PRVG-|ORA-|ASMCMD|ASMCA|failed|failure' || true",
        "      done",
        "      echo \"Captured executeConfigTools output: $CONFIG_TOOLS_LOG\"",
        "      exit \"$config_tools_rc\"",
        "    fi",
        "  fi",
        "fi",
        f"sudo -iu grid {crs_check}",
        f"sudo -iu grid {GRID_BASE}/bin/asmcmd lsdg || true",
    ]
    return shell_script("Run Grid configuration tools", lines)


def _crs_check_command(config: AutomationConfig) -> str:
    target = "crs" if config.install_type == "rac" else "has"
    return f"{GRID_BASE}/bin/crsctl check {target}"


def _single_gi_direct_asmca_lines(config: AutomationConfig) -> list[str]:
    if config.install_type != "single-gi":
        return []
    initial_group = "DATA"
    initial_disks = ",".join(
        asm_disk_spec(label)
        for label, _path, group, _disk in asm_entries(config)
        if group == initial_group
    )
    return [
        f"  if sudo -iu grid {GRID_BASE}/bin/crsctl check has >/dev/null 2>&1 && ! sudo -iu grid {GRID_BASE}/bin/asmcmd lsdg >/dev/null 2>&1; then",
        "    echo 'Running ASMCA directly with current ASMLIB disk string to avoid stale OUI config replay'",
        f"    sudo -iu grid env ORACLE_BASE={GRID_BASE_DIR} {GRID_BASE}/bin/asmca -silent -configureASM -sysAsmPassword \"$ASMSNMP_PASSWORD\" -asmsnmpPassword \"$ASMSNMP_PASSWORD\" -diskString {shlex.quote(asm_discovery_string(config))} -diskGroupName {initial_group} -diskList {shlex.quote(initial_disks)} -redundancy {shlex.quote(config.asm.redundancy)} -au_size 1",
        "  fi",
    ]


def _grid_known_hosts_lines(site: SiteConfig) -> list[str]:
    hostnames = sorted({name for node in site.nodes for name in (node.host, node.short_name) if name})
    host_args = " ".join(shlex.quote(hostname) for hostname in hostnames)
    return [
        "  echo 'Seeding grid SSH known_hosts for Oracle CVU strict host checks'",
        "  install -d -m 700 -o grid -g oinstall /home/grid/.ssh",
        "  touch /home/grid/.ssh/known_hosts",
        "  chown grid:oinstall /home/grid/.ssh/known_hosts",
        "  chmod 600 /home/grid/.ssh/known_hosts",
        "  touch /etc/ssh/ssh_known_hosts",
        "  chmod 644 /etc/ssh/ssh_known_hosts",
        f"  for host in {host_args}; do",
        "    if ! sudo -iu grid ssh-keygen -F \"$host\" >/dev/null 2>&1; then",
        "      ssh-keyscan -T 10 -t rsa,ecdsa,ed25519 \"$host\" 2>/dev/null >> /home/grid/.ssh/known_hosts || true",
        "    fi",
        "    ssh-keyscan -T 10 -t rsa,ecdsa,ed25519 \"$host\" 2>/dev/null >> /etc/ssh/ssh_known_hosts || true",
        "  done",
        "  sort -u /etc/ssh/ssh_known_hosts -o /etc/ssh/ssh_known_hosts || true",
        "  chown grid:oinstall /home/grid/.ssh/known_hosts",
        "  chmod 600 /home/grid/.ssh/known_hosts",
    ]


def _scan_dns_guard(site: SiteConfig) -> str:
    if not site.scan_name:
        return "true"
    return f"getent hosts {shlex.quote(site.scan_name)}"


def _hosts_guard(config: AutomationConfig) -> str:
    checks: list[str] = []
    for node in config.all_nodes:
        checks.append(f"grep -qw -- {shlex.quote(node.host)} /etc/hosts")
        if node.private_ip:
            checks.append(f"grep -qw -- {shlex.quote(node.private_hostname)} /etc/hosts")
        if node.vip_ip:
            checks.append(f"grep -qw -- {shlex.quote(node.vip_hostname)} /etc/hosts")
    return " && ".join(checks)
