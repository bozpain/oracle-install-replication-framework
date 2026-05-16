"""ASM storage phase manual.

Labels persistent device paths with Oracle ASMLIB. Physical multipath hosts get
stable `/dev/asm/<LABEL>` udev symlinks from DM_UUID first; non-multipath hosts
label the configured by-id/ID_SERIAL/ID_WWN device directly. Grid and ASM then
consume the ASMLIB logical discovery string (`ORCL:*`) instead of OS device
paths.
The old `prepare-storage` command remains as a compatibility wrapper for
dry-run review.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import ASMDiskConfig, AutomationConfig, NodeConfig, SiteConfig
from oracle_auto.phase_builders.common import GRID_BASE, GRID_BASE_DIR, install_asmlib_lines, make_step


ASMEntry = tuple[str, str, str, ASMDiskConfig]
ASM_DEVICE_GROUP = "asmdba"
ASM_UDEV_GROUP = "asmadmin"
ASM_UDEV_RULES = "/etc/udev/rules.d/99-oracle-asm.rules"


def prepare_storage_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [*prepare_storage_rules_steps(config), *configure_asm_storage_steps(config)]


def prepare_storage_rules_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        make_step(
            "prepare-storage-rules",
            "prepare_persistent_storage",
            node,
            "Prepare persistent ASM device paths for ASMLIB",
            _prepare_storage_rules_script(config, node),
            timeout=300,
        )
        for node in config.all_nodes
    ]


def configure_asm_storage_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        make_step(
                "configure-asm-storage",
                "configure_asm_storage",
                node,
                "Configure ASMLIB labels and ASM disk groups",
                _configure_asm_storage_script(config, node),
                timeout=1200,
            )
        for node in config.all_nodes
    ]


def asm_entries(
    config: AutomationConfig,
    site: SiteConfig | None = None,
    node: NodeConfig | None = None,
) -> list[ASMEntry]:
    entries: list[ASMEntry] = []
    site_name = site.name if site else None
    node_host = node.host if node else None
    for group, disks in (
        ("OCR", config.asm.ocr_disks),
        ("DATA", config.asm.data_disks),
        ("RECO", config.asm.reco_disks),
    ):
        for index, disk in enumerate(disks, start=1):
            label = disk.symlink_name(group, index).upper()
            entries.append((label, disk.final_path(group, index, site_name=site_name, node_host=node_host), group, disk))
    return entries


def storage_mapping_text(
    config: AutomationConfig,
    site: SiteConfig | None = None,
    node: NodeConfig | None = None,
) -> str:
    rows = []
    if site:
        sites = [site]
    else:
        sites = config.sites
    for item_site in sites:
        item_node = node if node and any(site_node.host == node.host for site_node in item_site.nodes) else None
        for label, path, group, disk in asm_entries(config, item_site, item_node):
            source = disk.source_for(site_name=item_site.name, node_host=item_node.host if item_node else None)
            rows.append(f"{item_site.name:8} {group:4} {label:16} {source} -> {path}")
    return "\n".join(rows)


def asm_device_permission_commands(paths: list[str]) -> list[str]:
    commands: list[str] = []
    for path in paths:
        quoted_path = shlex.quote(path)
        commands.extend(
            [
                f"chown -h grid:{ASM_DEVICE_GROUP} {quoted_path}",
                f"resolved=$(readlink -f {quoted_path}); test -b \"$resolved\"; chown grid:{ASM_DEVICE_GROUP} \"$resolved\"; chmod 0660 \"$resolved\"",
                f"sudo -iu grid test -r {quoted_path}",
            ]
        )
    return commands


def asm_discovery_string(_config: AutomationConfig) -> str:
    return "ORCL:*"


def asm_disk_spec(label: str) -> str:
    return f"ORCL:{label}"


def grid_env_command(command: str) -> str:
    return (
        "sudo -iu grid env "
        f"ORACLE_HOME={GRID_BASE} "
        f"ORACLE_BASE={GRID_BASE_DIR} "
        'ORACLE_SID="$ASM_SID" '
        f"PATH={GRID_BASE}/bin:/usr/local/bin:/usr/bin:/bin "
        f"LD_LIBRARY_PATH={GRID_BASE}/lib "
        f"{command}"
    )


def asm_sid_detection_lines() -> list[str]:
    return [
        "ASM_SID=$(ps -ef | awk '/[a]sm_pmon_/ {sub(/^.*asm_pmon_/, \"\", $0); print $0; exit}')",
        "if test -z \"$ASM_SID\"; then",
        "  echo 'ASM instance process not detected yet; attempting to start ASM resource.'",
        f"  sudo -iu grid env ORACLE_HOME={GRID_BASE} ORACLE_BASE={GRID_BASE_DIR} PATH={GRID_BASE}/bin:/usr/local/bin:/usr/bin:/bin LD_LIBRARY_PATH={GRID_BASE}/lib {GRID_BASE}/bin/srvctl start asm || true",
        "  sleep 5",
        "  ASM_SID=$(ps -ef | awk '/[a]sm_pmon_/ {sub(/^.*asm_pmon_/, \"\", $0); print $0; exit}')",
        "fi",
        "test -n \"$ASM_SID\"",
        "echo \"Using ASM SID: $ASM_SID\"",
    ]


def create_diskgroup_sql(name: str, labels: list[str], redundancy: str) -> str:
    disk_list = ",".join(f"'{asm_disk_spec(label)}'" for label in labels)
    return (
        f"{grid_env_command(f'{GRID_BASE}/bin/sqlplus -s / as sysasm')} <<'SQL'\n"
        "WHENEVER SQLERROR EXIT SQL.SQLCODE\n"
        "SET SERVEROUTPUT ON\n"
        f"DECLARE\n  existing NUMBER;\nBEGIN\n  SELECT COUNT(*) INTO existing FROM v$asm_diskgroup WHERE name = '{name}';\n"
        "  IF existing = 0 THEN\n"
        f"    DBMS_OUTPUT.PUT_LINE('Creating diskgroup {name} with ASMLIB disks: {', '.join(labels)}');\n"
        f"    EXECUTE IMMEDIATE q'[CREATE DISKGROUP {name} {redundancy} REDUNDANCY DISK {disk_list} ATTRIBUTE 'compatible.asm'='19.0','compatible.rdbms'='19.0','compatible.advm'='19.0']';\n"
        "  ELSE\n"
        f"    DBMS_OUTPUT.PUT_LINE('Diskgroup {name} already exists; skipping create.');\n"
        "  END IF;\nEND;\n/\nSQL"
    )


def asm_diskstring_sql() -> str:
    return (
        f"{grid_env_command(f'{GRID_BASE}/bin/sqlplus -s / as sysasm')} <<'SQL'\n"
        "WHENEVER SQLERROR EXIT SQL.SQLCODE\n"
        "ALTER SYSTEM SET asm_diskstring='ORCL:*' SCOPE=BOTH;\n"
        "SQL"
    )


def asmlib_kernel_check_command() -> str:
    return (
        "kernel=$(uname -r)\n"
        "if [[ \"$kernel\" == *uek* ]]; then\n"
        "  case \"$kernel\" in\n"
        "    5.15.*|6.*|7.*) echo \"ASMLIB v3 kernel interface: UEK driverless/io_uring ($kernel)\" ;;\n"
        "    *) echo \"ASMLIB v3 requires UEK R7+ (5.15+) or an oracleasm kernel driver; current kernel: $kernel\" >&2; exit 1 ;;\n"
        "  esac\n"
        "elif find \"/lib/modules/$kernel\" -name 'oracleasm.ko*' -print -quit 2>/dev/null | grep -q .; then\n"
        "  echo \"ASMLIB kernel driver present for $kernel\"\n"
        "else\n"
        "  echo \"ASMLIB v3 requires UEK R7+ (5.15+) or an oracleasm kernel driver; current kernel: $kernel. Boot the UEK kernel, for example /boot/vmlinuz-5.15.0-320.202.8.2.el8uek.x86_64.\" >&2\n"
        "  exit 1\n"
        "fi"
    )


def asmlib_label_command(label: str, disk: ASMDiskConfig, path: str) -> str:
    quoted_label = shlex.quote(label)
    configured_path = path if disk.path or disk.site_paths or disk.node_paths or disk.uuid else ""
    args = " ".join(
        shlex.quote(value)
        for value in (
            label,
            configured_path,
            disk.dm_uuid if disk.uuid else "",
            disk.id_serial or "",
            disk.id_wwn or "",
        )
    )
    return (
        f"resolved=$(resolve_asm_source_device {args}); test -b \"$resolved\"; "
        f"if oracleasm querydisk {quoted_label} >/dev/null 2>&1; then "
        f"echo 'ASMLIB disk already exists: {label}'; "
        "else "
        f"oracleasm createdisk {quoted_label} \"$resolved\"; "
        "fi; "
        "oracleasm scandisks; "
        f"oracleasm querydisk {quoted_label}"
    )


def _multipath_detection_lines() -> list[str]:
    return [
        "ORACLE_AUTO_MULTIPATH=false",
        "MULTIPATH_PROBE=$(mktemp /tmp/oracle-auto-multipath.XXXXXX)",
        "if command -v multipath >/dev/null 2>&1 && multipath -ll >\"$MULTIPATH_PROBE\" 2>/dev/null && test -s \"$MULTIPATH_PROBE\"; then",
        "  ORACLE_AUTO_MULTIPATH=true",
        "fi",
        "echo \"Oracle ASM storage mode: $(test \"$ORACLE_AUTO_MULTIPATH\" = true && echo multipath-udev || echo direct-asmlib)\"",
        "cat \"$MULTIPATH_PROBE\" || true",
    ]


def _asm_device_resolver_function() -> str:
    return r"""resolve_asm_source_device() {
  label="$1"
  configured_path="$2"
  dm_uuid="$3"
  id_serial="$4"
  id_wwn="$5"

  if test "${ORACLE_AUTO_MULTIPATH:-false}" = true; then
    test -b "/dev/asm/$label"
    readlink -f "/dev/asm/$label"
    return
  fi

  if test -n "$configured_path"; then
    test -e "$configured_path"
    readlink -f "$configured_path"
    return
  fi

  for candidate in /dev/disk/by-id/*; do
    test -e "$candidate" || continue
    props=$(udevadm info --query=property --name "$candidate" 2>/dev/null || true)
    if test -n "$id_serial" && printf '%s\n' "$props" | grep -Fxq "ID_SERIAL=$id_serial"; then
      readlink -f "$candidate"
      return
    fi
    if test -n "$id_wwn" && printf '%s\n' "$props" | grep -Fxq "ID_WWN=$id_wwn"; then
      readlink -f "$candidate"
      return
    fi
    if test -n "$dm_uuid" && printf '%s\n' "$props" | grep -Fxq "DM_UUID=$dm_uuid"; then
      readlink -f "$candidate"
      return
    fi
  done

  echo "ERROR: Unable to resolve ASM disk $label from path, DM_UUID, ID_SERIAL, or ID_WWN." >&2
  exit 1
}"""


def _multipath_udev_lines(entries: list[ASMEntry]) -> list[str]:
    rules = "\n".join(
        f'KERNEL=="dm-*", ENV{{DM_UUID}}=="{disk.dm_uuid}", SYMLINK+="asm/{label}", OWNER:="grid", GROUP:="{ASM_UDEV_GROUP}", MODE="0660"'
        for label, _path, _group, disk in entries
        if disk.uuid
    )
    missing_uuid_labels = " ".join(shlex.quote(label) for label, _path, _group, disk in entries if not disk.uuid)
    labels = " ".join(shlex.quote(label) for label, _path, _group, _disk in entries)
    return [
        "if test \"$ORACLE_AUTO_MULTIPATH\" = true; then",
        "  echo 'Multipath detected; writing /dev/asm udev rules from configured DM_UUID values.'",
        f"  missing_uuid_labels={shlex.quote(missing_uuid_labels)}",
        "  if test -n \"$missing_uuid_labels\"; then echo \"ERROR: Multipath mode requires uuid for ASM label(s): $missing_uuid_labels\" >&2; exit 1; fi",
        f"  cat > {ASM_UDEV_RULES} <<'EOF'\n{rules}\nEOF",
        f"  chmod 0644 {ASM_UDEV_RULES}",
        "  udevadm control --reload-rules",
        "  udevadm trigger",
        "  udevadm settle || true",
        f"  for label in {labels}; do",
        "    test -b \"/dev/asm/$label\"",
        f"    chown -h grid:{ASM_UDEV_GROUP} \"/dev/asm/$label\"",
        "    resolved=$(readlink -f \"/dev/asm/$label\"); test -b \"$resolved\"",
        f"    chown grid:{ASM_UDEV_GROUP} \"$resolved\"; chmod 0660 \"$resolved\"",
        "    sudo -iu grid test -r \"/dev/asm/$label\"",
        "  done",
        "else",
        "  echo 'No multipath devices detected; ASMLIB will label the configured by-id/ID_SERIAL/ID_WWN devices directly.'",
        "fi",
    ]


def _prepare_storage_rules_script(config: AutomationConfig, node: NodeConfig) -> str:
    site = config.site_for_node(node)
    entries = asm_entries(config, site, node)
    uuid_checks = [
        f"test -e {shlex.quote(path)} || udevadm info --export-db | grep -q {shlex.quote('DM_UUID=' + disk.dm_uuid)}"
        for _label, path, _group, disk in entries
        if disk.uuid
    ]
    lines = [
        "command -v udevadm",
        *install_asmlib_lines(config.os.package_manager, config.installer.sources_path, config.os.asmlib_rpms),
        "command -v oracleasm",
        asmlib_kernel_check_command(),
        *_multipath_detection_lines(),
        _asm_device_resolver_function(),
        "echo 'Planned ASM disk mapping:'",
        "cat <<'MAP'\n" + storage_mapping_text(config, site, node) + "\nMAP",
        *uuid_checks,
        *_multipath_udev_lines(entries),
        "oracleasm configure -u grid -g asmdba -e -s y -m 2048",
        "systemctl restart oracleasm || oracleasm init",
        "systemctl enable oracleasm || true",
        "oracleasm status || true",
        *[asmlib_label_command(label, disk, path) for label, path, _group, disk in entries],
        "oracleasm listdisks",
        "oracleasm status || true",
    ]
    return shell_script("Prepare ASMLIB disks", lines)


def _configure_asm_storage_script(config: AutomationConfig, node: NodeConfig) -> str:
    site = config.site_for_node(node)
    entries = asm_entries(config, site, node)
    crs_check = f"{GRID_BASE}/bin/crsctl check {'crs' if config.install_type == 'rac' else 'has'}"
    diskgroup_commands = []
    for diskgroup_name in ("OCR", "DATA", "RECO"):
        labels = [label for label, _path, group, _disk in entries if group == diskgroup_name]
        if labels:
            diskgroup_commands.append(create_diskgroup_sql(diskgroup_name, labels, config.asm.redundancy))
    lines = [
        "oracleasm scandisks",
        "oracleasm listdisks",
        *[f"oracleasm querydisk {shlex.quote(label)}" for label, _path, _group, _disk in entries],
        f"test -x {GRID_BASE}/bin/sqlplus",
        f"sudo -iu grid {crs_check}",
        *asm_sid_detection_lines(),
        grid_env_command(f"{GRID_BASE}/bin/srvctl status asm") + " || true",
        asm_diskstring_sql(),
        *diskgroup_commands,
        grid_env_command(f"{GRID_BASE}/bin/asmcmd lsdg"),
    ]
    return shell_script("Configure ASM diskgroups with ASMLIB", lines)
