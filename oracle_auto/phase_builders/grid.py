"""Grid Infrastructure phase manual.

Builds Grid Infrastructure response files and root script execution steps. RAC
and single-GI share this module because both use GI and ASM.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig, NodeConfig, SiteConfig
from oracle_auto.phase_builders.common import (
    GRID_BASE,
    GRID_BASE_DIR,
    STAGE,
    ensure_swap_lines,
    inventory_pointer_lines,
    make_step,
    oracle_home_inventory_pointer_lines,
    stage_patch_lines,
)
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
                remote_marker=False,
                force_rerun=True,
            )
        )
        for node in site.nodes:
            steps.append(
                make_step(
                    "install-grid",
                    f"root_scripts_{site.name}_{node.short_name}",
                    node,
                    f"Run Grid root scripts for {node.host}",
                    _grid_root_script(config, site, node),
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
        *_pre_grid_vip_cleanup_lines(site, site.nodes[0]),
        *_temporary_network_anchor_lines(site, site.nodes[0]),
        *ensure_swap_lines(),
        *inventory_pointer_lines(),
        *_fresh_grid_home_lines(config),
        *oracle_home_inventory_pointer_lines(GRID_BASE, "grid"),
        *_grid_opatch_lines(config),
        *oracle_home_inventory_pointer_lines(GRID_BASE, "grid"),
        *_grid_patch_stage_lines(config),
        f"cat > {STAGE}/responses/grid-{site.name}.rsp <<EOF\n{response}\nEOF",
        f"chown grid:oinstall {STAGE}/responses/grid-{site.name}.rsp",
        f"chmod 600 {STAGE}/responses/grid-{site.name}.rsp",
        f"mkdir -p {STAGE}/logs",
        f"GRID_SETUP_LOG={STAGE}/logs/gridSetup-{site.name}.out",
        *_grid_ru_applied_detection_lines(config),
        "GRID_SOFTWARE_READY=false",
        "if test -f /etc/oracle/olr.loc; then",
        "  echo 'Grid Infrastructure is already configured; preserving Grid home and skipping software setup.'",
        "  GRID_SOFTWARE_READY=true",
        "elif test -x "
        f"{GRID_BASE}/root.sh && ls {GRID_BASE}/install/response/grid_*.rsp >/dev/null 2>&1 && test \"$GRID_RU_APPLIED\" = true; then",
        "  echo 'Grid software and RU already installed; skipping software setup and continuing with root scripts/config tools.'",
        "  GRID_SOFTWARE_READY=true",
        "fi",
        "if test \"$GRID_SOFTWARE_READY\" = true; then",
        "  true",
        "else",
        "  echo 'Running Grid software setup with RU apply when configured.'",
        "  set +e",
        f"  sudo -iu grid env CV_ASSUME_DISTID=OL7 ORACLE_BASE={GRID_BASE_DIR} {GRID_BASE}/gridSetup.sh -silent -waitforcompletion -responseFile {STAGE}/responses/grid-{site.name}.rsp{_grid_patch_arg(config)} -ignorePrereqFailure 2>&1 | tee \"$GRID_SETUP_LOG\"",
        "  grid_setup_rc=${PIPESTATUS[0]}",
        "  set -e",
        "  if test \"$grid_setup_rc\" -ne 0; then",
        "    if grep -Eq 'Successfully Setup Software|execute the following script|executeConfigTools' \"$GRID_SETUP_LOG\" && test -x "
        f"{GRID_BASE}/root.sh; then",
        "      echo 'Grid setup reached root/config-tool phase; validating RU version before continuing.'",
        "    else",
        "      echo \"ERROR: Grid software setup failed before root/config-tool phase. See $GRID_SETUP_LOG\" >&2",
        "      exit \"$grid_setup_rc\"",
        "    fi",
        "  fi",
        "fi",
        *_grid_ru_validation_lines(config),
    ]
    return shell_script(f"Install Grid Infrastructure for {site.name}", lines)


def _grid_patch_stage_lines(config: AutomationConfig) -> list[str]:
    if config.installer.grid_patch is None:
        return []
    return [
        *stage_patch_lines(
            config.installer.sources_path,
            config.installer.grid_patch.file,
            "GRID_PATCH_TOP",
            patch_id=config.installer.grid_patch.patch_id,
        ),
        'ls -ld "$GRID_PATCH_TOP"',
        'namei -l "$GRID_PATCH_TOP" || true',
        'sudo -iu grid test -d "$GRID_PATCH_TOP"',
        'sudo -iu grid ls -ld "$GRID_PATCH_TOP"',
    ]


def _fresh_grid_home_lines(config: AutomationConfig) -> list[str]:
    grid_zip = f"{config.installer.sources_path}/{config.installer.grid_zip}"
    return [
        "if test -f /etc/oracle/olr.loc; then",
        "  echo 'Grid Infrastructure already configured; not cleaning Grid home.'",
        "else",
        f"  if test -d {GRID_BASE} && find {GRID_BASE} -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then",
        "    echo 'Cleaning unconfigured Grid home before install/resume.'",
        f"    find {GRID_BASE} -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} +",
        "  fi",
        f"  sudo -iu grid unzip -oq {shlex.quote(grid_zip)} -d {GRID_BASE}",
        "fi",
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


def _grid_ru_applied_detection_lines(config: AutomationConfig) -> list[str]:
    lines = ["GRID_RU_APPLIED=false"]
    if config.installer.grid_patch is None:
        return lines
    return [
        *lines,
        f"if test -x {GRID_BASE}/bin/oraversion; then",
        f"  grid_version=$(sudo -iu grid {GRID_BASE}/bin/oraversion -compositeVersion 2>/dev/null || sudo -iu grid {GRID_BASE}/bin/oraversion -version 2>/dev/null || true)",
        "  if test -n \"$grid_version\"; then",
        "    echo \"Grid Oracle version: $grid_version\"",
        "    case \"$grid_version\" in *19.3.0.0.0*) ;; *) GRID_RU_APPLIED=true ;; esac",
        "  fi",
        "fi",
    ]


def _grid_ru_validation_lines(config: AutomationConfig) -> list[str]:
    if config.installer.grid_patch is None:
        return [
            "echo 'No Grid RU configured; skipping RU validation.'",
        ]

    return [
        "echo 'Validating Grid RU with oraversion before root scripts/config tools.'",
        f"grid_version=$(sudo -iu grid {GRID_BASE}/bin/oraversion -compositeVersion 2>/dev/null || sudo -iu grid {GRID_BASE}/bin/oraversion -version 2>/dev/null || true)",
        "echo \"Grid Oracle version: ${grid_version:-unknown}\"",
        "if test -z \"$grid_version\"; then",
        "  echo 'ERROR: Grid oraversion did not return a version after applyRU. Refusing to continue.' >&2",
        "  exit 1",
        "fi",
        "case \"$grid_version\" in",
        "  *19.3.0.0.0*) echo 'ERROR: Grid home still reports 19.3.0.0.0 after applyRU. Refusing to continue.' >&2; exit 1 ;;",
        "esac",
        "echo 'Grid RU validation passed by oraversion.'",
    ]


def _asm_password_export(config: AutomationConfig) -> str:
    return (
        f'ASMSNMP_PASSWORD="${{{config.secrets.asmsnmp_password_env}:?'
        f'Set {config.secrets.asmsnmp_password_env} on target before running install-grid}}"\n'
        "export ASMSNMP_PASSWORD"
    )


def _grid_root_script(config: AutomationConfig, site: SiteConfig, node) -> str:
    crs_check = _crs_check_command(config)
    lines = [
        *_pre_grid_vip_cleanup_lines(site, node),
        *_temporary_network_anchor_lines(site, node),
        "test -x /u01/app/oraInventory/orainstRoot.sh && /u01/app/oraInventory/orainstRoot.sh || true",
        f"test -x {GRID_BASE}/root.sh",
        f"if sudo -iu grid {crs_check} >/dev/null 2>&1; then echo 'Grid appears active; skipping root.sh rerun.'; else {GRID_BASE}/root.sh; fi",
        f"sudo -iu grid {crs_check} || true",
        f"sudo -iu grid {GRID_BASE}/bin/asmcmd lsdg || true",
    ]
    return shell_script("Run Grid root scripts", lines)


def _grid_config_tools_script(config: AutomationConfig, site: SiteConfig) -> str:
    response = grid_response(config, site)
    response_file = f"{STAGE}/responses/grid-{site.name}.rsp"
    crs_check = _crs_check_command(config)
    lines = [
        _asm_password_export(config),
        *_grid_ru_validation_lines(config),
        *_temporary_network_anchor_lines(site, site.nodes[0]),
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
        "  GRID_CONFIG_TOOLS_COMPLETE=false",
        *_single_gi_existing_asm_skip_lines(config),
        *_single_gi_direct_asmca_lines(config),
        f"  if test \"$GRID_CONFIG_TOOLS_COMPLETE\" = true || (sudo -iu grid {crs_check} >/dev/null 2>&1 && sudo -iu grid {GRID_BASE}/bin/asmcmd lsdg >/dev/null 2>&1); then",
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
        asm_disk_spec(config, label, path)
        for label, path, group, _disk in asm_entries(config, config.primary_site, config.primary_site.nodes[0])
        if group == initial_group
    )
    disk_string = asm_discovery_string(config, config.primary_site, config.primary_site.nodes[0])

    return [
        f"  if test \"$GRID_CONFIG_TOOLS_COMPLETE\" != true && sudo -iu grid {GRID_BASE}/bin/crsctl check has >/dev/null 2>&1 && ! sudo -iu grid {GRID_BASE}/bin/asmcmd lsdg >/dev/null 2>&1; then",
        "    echo 'Running ASMCA directly with configured ASM disk string'",
        "    echo 'ASM disk string: " + disk_string + "'",
        "    echo 'ASM disk list: " + initial_disks + "'",
        "    DIRECT_ASMCA_LOG=$(mktemp /tmp/oracle-auto-direct-asmca.XXXXXX)",
        "    set +e",
        f"    sudo -iu grid env ORACLE_BASE={GRID_BASE_DIR} {GRID_BASE}/bin/asmca -silent -configureASM -sysAsmPassword \"$ASMSNMP_PASSWORD\" -asmsnmpPassword \"$ASMSNMP_PASSWORD\" -diskString {shlex.quote(disk_string)} -diskGroupName {initial_group} -diskList {shlex.quote(initial_disks)} -redundancy {shlex.quote(config.asm.redundancy)} -au_size 1 2>&1 | tee \"$DIRECT_ASMCA_LOG\"",
        "    direct_asmca_rc=${PIPESTATUS[0]}",
        "    set -e",
        "    if test \"$direct_asmca_rc\" -ne 0 && grep -q 'DBT-30017.*Disk group DATA already exists' \"$DIRECT_ASMCA_LOG\"; then",
        "      echo 'ASMCA reports DATA already exists; treating single-GI ASM config as complete.'",
        "      GRID_CONFIG_TOOLS_COMPLETE=true",
        "      direct_asmca_rc=0",
        "    fi",
        f"    if test \"$direct_asmca_rc\" -ne 0 && ! sudo -iu grid {GRID_BASE}/bin/asmcmd lsdg >/dev/null 2>&1; then",
        "      echo 'ERROR: ASMCA failed using configured ASM discovery. Not retrying with another storage mode.' >&2",
        "      oracleasm status || true",
        "      oracleasm listdisks || true",
        "      exit \"$direct_asmca_rc\"",
        "    fi",
        "  fi",
    ]


def _single_gi_existing_asm_skip_lines(config: AutomationConfig) -> list[str]:
    if config.install_type != "single-gi":
        return []
    return [
        f"  if sudo -iu grid {GRID_BASE}/bin/asmcmd lsdg DATA >/dev/null 2>&1 || sudo -iu grid {GRID_BASE}/bin/asmcmd lsdg 2>/dev/null | awk 'NR > 1 {{print $NF}}' | grep -qx DATA; then",
        "    echo 'Single-GI ASM DATA diskgroup already exists; treating ASM config tools as complete and skipping OUI ASMCA replay.'",
        "    GRID_CONFIG_TOOLS_COMPLETE=true",
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


def _pre_grid_vip_cleanup_lines(site: SiteConfig, node: NodeConfig) -> list[str]:
    if not node.vip_ip:
        return []

    quoted_vip = shlex.quote(node.vip_ip)
    public_interface = _public_interface_name(site)
    interface_part = f" dev {shlex.quote(public_interface)}" if public_interface else ""
    return [
        "if ! test -f /etc/oracle/olr.loc; then",
        f"  if ip route show table local | grep -Fq 'local {node.vip_ip} '; then",
        f"    echo 'Removing pre-bound local VIP route {node.vip_ip} before Grid install.'",
        f"    if ip route show table local | grep -F 'local {node.vip_ip} ' | grep -Fq ' proto 66 '; then",
        "      echo 'Stopping Google guest agent network management while CRS takes ownership of VIPs.'",
        "      systemctl stop google-guest-agent-manager google-guest-compat-manager 2>/dev/null || true",
        "      pkill -f '[G]uestAgentCorePlugin' 2>/dev/null || true",
        "    fi",
        f"    ip route del local {quoted_vip}{interface_part} table local proto 66 2>/dev/null || "
        f"ip route del local {quoted_vip}{interface_part} table local 2>/dev/null || "
        f"ip route del local {quoted_vip} table local 2>/dev/null || true",
        "  fi",
        "fi",
    ]


def _public_interface_name(site: SiteConfig) -> str | None:
    if not site.network_interface_list:
        return None
    for entry in site.network_interface_list.split(","):
        interface_name, _subnet, interface_type = entry.split(":")
        if interface_type == "1":
            return interface_name
    return None


def _temporary_network_anchor_lines(site: SiteConfig, node) -> list[str]:
    if not site.network_interface_list:
        return []

    anchors: list[tuple[str, str]] = []
    for entry in site.network_interface_list.split(","):
        interface_name, subnet, interface_type = entry.split(":")
        if "/" not in subnet:
            continue
        prefix_length = subnet.split("/", 1)[1]
        if interface_type == "1":
            address = node.public_subnet_anchor_ip or node.public_ip
        else:
            address = node.private_subnet_anchor_ip or node.private_ip
        if address:
            anchors.append((interface_name, f"{address}/{prefix_length}"))

    if not anchors:
        return []

    lines = [
        "ORACLE_AUTO_NET_ANCHORS=()",
        "cleanup_oracle_auto_net_anchors() {",
        '  for item in "${ORACLE_AUTO_NET_ANCHORS[@]}"; do',
        '    read -r iface cidr <<< "$item"',
        '    ip addr del "$cidr" dev "$iface" 2>/dev/null || true',
        "  done",
        "}",
        "trap cleanup_oracle_auto_net_anchors EXIT",
    ]
    for interface_name, cidr in anchors:
        quoted_interface = shlex.quote(interface_name)
        quoted_cidr = shlex.quote(cidr)
        quoted_anchor = shlex.quote(f"{interface_name} {cidr}")
        quoted_label = shlex.quote(f"{interface_name}:0")
        lines.extend(
            [
                f"if ! ip -o -4 addr show dev {quoted_interface} | awk '{{print $4}}' | grep -Fxq {quoted_cidr}; then",
                f"  ip addr add {quoted_cidr} brd + dev {quoted_interface} label {quoted_label} noprefixroute 2>/dev/null && ORACLE_AUTO_NET_ANCHORS+=({quoted_anchor}) || true",
                "fi",
            ]
        )
    return lines


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
