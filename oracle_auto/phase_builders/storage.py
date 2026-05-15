"""ASM storage phase manual.

Builds UUID-driven udev rules first, then labels disks with Oracle ASMLIB.
The old `prepare-storage` command remains as a compatibility wrapper for
dry-run review.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import ASMDiskConfig, AutomationConfig
from oracle_auto.phase_builders.common import GRID_BASE, install_asmlib_lines, make_step


ASMEntry = tuple[str, str, str, ASMDiskConfig]
ASM_DEVICE_GROUP = "asmdba"


def prepare_storage_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [*prepare_storage_rules_steps(config), *configure_asm_storage_steps(config)]


def prepare_storage_rules_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        make_step(
            "prepare-storage-rules",
            "prepare_udev_rules",
            node,
            "Prepare UUID-driven udev rules for Oracle ASM disks",
            _prepare_storage_rules_script(config),
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
                _configure_asm_storage_script(config),
                timeout=1200,
            )
        for node in config.all_nodes
    ]


def asm_entries(config: AutomationConfig) -> list[ASMEntry]:
    entries: list[ASMEntry] = []
    for group, disks in (
        ("OCR", config.asm.ocr_disks),
        ("DATA", config.asm.data_disks),
        ("RECO", config.asm.reco_disks),
    ):
        for index, disk in enumerate(disks, start=1):
            label = disk.symlink_name(group, index).upper()
            entries.append((label, disk.final_path(group, index), group, disk))
    return entries


def storage_mapping_text(config: AutomationConfig) -> str:
    rows = []
    for label, path, group, disk in asm_entries(config):
        source = disk.dm_uuid if disk.uuid else disk.path
        rows.append(f"{group:4} {label:16} {source} -> {path}")
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
    return "/dev/oracleasm/*"


def asm_disk_spec(label: str) -> str:
    return f"/dev/oracleasm/{label.lower()}"


def create_diskgroup_sql(name: str, labels: list[str], redundancy: str) -> str:
    disk_list = ",".join(f"'{asm_disk_spec(label)}'" for label in labels)
    return (
        f"sudo -iu grid {GRID_BASE}/bin/sqlplus -s / as sysasm <<'SQL'\n"
        "WHENEVER SQLERROR EXIT SQL.SQLCODE\n"
        f"DECLARE\n  existing NUMBER;\nBEGIN\n  SELECT COUNT(*) INTO existing FROM v$asm_diskgroup WHERE name = '{name}';\n"
        "  IF existing = 0 THEN\n"
        f"    EXECUTE IMMEDIATE q'[CREATE DISKGROUP {name} {redundancy} REDUNDANCY DISK {disk_list} ATTRIBUTE 'compatible.asm'='19.0','compatible.rdbms'='19.0','compatible.advm'='19.0']';\n"
        "  END IF;\nEND;\n/\nSQL"
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


def asmlib_label_command(label: str, path: str) -> str:
    quoted_label = shlex.quote(label)
    quoted_path = shlex.quote(path)
    return (
        f"resolved=$(readlink -f {quoted_path}); test -b \"$resolved\"; "
        f"if test -x {GRID_BASE}/bin/asmcmd && "
        f"ORACLE_HOME={GRID_BASE} ORACLE_BASE=/tmp {GRID_BASE}/bin/asmcmd afd_lslbl \"$resolved\" 2>/dev/null | "
        f"awk '{{print $1}}' | grep -qx {quoted_label}; then "
        f"echo 'Removing stale ASMFD label before ASMLIB migration: {label}'; "
        f"ORACLE_HOME={GRID_BASE} ORACLE_BASE=/tmp {GRID_BASE}/bin/asmcmd afd_unlabel \"$resolved\" --init; "
        "fi; "
        f"if oracleasm querydisk {quoted_label} >/dev/null 2>&1; then "
        f"echo 'ASMLIB disk already exists: {label}'; "
        "else "
        f"oracleasm createdisk {quoted_label} \"$resolved\"; "
        "fi; "
        "oracleasm scandisks; "
        f"oracleasm querydisk {quoted_label}"
    )


def _prepare_storage_rules_script(config: AutomationConfig) -> str:
    entries = asm_entries(config)
    rules = _udev_rules(config)
    disk_checks = [f"test -b {shlex.quote(path)}" for _label, path, _group, _disk in entries]
    path_entries = [
        (path, disk.path)
        for _label, path, _group, disk in entries
        if disk.path and disk.path != path
    ]
    path_checks = [f"test -b {shlex.quote(source)}" for _path, source in path_entries]
    path_symlinks = [
        (
            f"ln -sfn \"$(readlink -f {shlex.quote(source)})\" {shlex.quote(path)} && "
            f"chown -h grid:{ASM_DEVICE_GROUP} {shlex.quote(path)} && "
            f"chown grid:{ASM_DEVICE_GROUP} \"$(readlink -f {shlex.quote(source)})\" && "
            f"chmod 0660 \"$(readlink -f {shlex.quote(source)})\""
        )
        for path, source in path_entries
    ]
    uuid_checks = [
        f"udevadm info --export-db | grep -q {shlex.quote('DM_UUID=' + disk.dm_uuid)}"
        for _label, _path, _group, disk in entries
        if disk.uuid
    ]
    collision_checks = [
        f"test ! -e {shlex.quote(path)} || test -b {shlex.quote(path)}"
        for _label, path, _group, _disk in entries
    ]
    managed_names = {path.rsplit("/", 1)[-1] for _label, path, _group, _disk in entries}
    managed_name_args = " ".join(shlex.quote(name) for name in sorted(managed_names))
    lines = [
        "command -v udevadm",
        *install_asmlib_lines(config.os.package_manager),
        "command -v oracleasm",
        asmlib_kernel_check_command(),
        "echo 'Planned ASM disk mapping:'",
        "cat <<'MAP'\n" + storage_mapping_text(config) + "\nMAP",
        *uuid_checks,
        *path_checks,
        "mkdir -p /dev/oracleasm",
        (
            f"for item in /dev/oracleasm/*; do test -e \"$item\" || continue; name=$(basename \"$item\"); "
            f"case \" {managed_name_args} \" in *\" $name \"*) ;; *) "
            "if test -L \"$item\"; then echo \"Removing unmanaged ASM symlink: $item\"; rm -f \"$item\"; fi ;; "
            "esac; done"
        ),
        *collision_checks,
        *(
            [
                "cat > /etc/udev/rules.d/99-oracleasm.rules <<'EOF'\n" + rules + "\nEOF",
                "udevadm control --reload-rules",
                "udevadm trigger --subsystem-match=block --action=change",
                "udevadm settle",
            ]
            if rules
            else []
        ),
        *path_symlinks,
        *asm_device_permission_commands([path for _label, path, _group, _disk in entries]),
        *disk_checks,
        "ls -l /dev/oracleasm",
        "oracleasm configure -u grid -g asmdba -e -s y -m 2048",
        "systemctl restart oracleasm || oracleasm init",
        "systemctl enable oracleasm || true",
        "oracleasm status || true",
        *[asmlib_label_command(label, path) for label, path, _group, _disk in entries],
        *asm_device_permission_commands([path for _label, path, _group, _disk in entries]),
        "oracleasm listdisks",
        "oracleasm status || true",
    ]
    return shell_script("Prepare ASMLIB disks", lines)


def _configure_asm_storage_script(config: AutomationConfig) -> str:
    entries = asm_entries(config)
    disk_checks = [f"test -b {shlex.quote(path)}" for _label, path, _group, _disk in entries]
    signature_checks = [
        f"test -z \"$(wipefs -n {shlex.quote(path)} 2>/dev/null | awk 'NR>1')\""
        for _label, path, _group, _disk in entries
    ]
    size_checks = [
        f"test \"$(blockdev --getsize64 {shlex.quote(path)})\" -gt 0"
        for _label, path, _group, _disk in entries
    ]
    diskgroup_commands = []
    for diskgroup_name in ("OCR", "DATA", "RECO"):
        labels = [label for label, _path, group, _disk in entries if group == diskgroup_name]
        if labels:
            diskgroup_commands.append(create_diskgroup_sql(diskgroup_name, labels, config.asm.redundancy))
    lines = [
        "echo 'Resolved ASM disk mapping before AFD label:'",
        "for path in " + " ".join(shlex.quote(path) for _label, path, _group, _disk in entries) + "; do printf '%s -> ' \"$path\"; readlink -f \"$path\"; done",
        *disk_checks,
        *size_checks,
        "oracleasm scandisks",
        "oracleasm listdisks",
        f"test -x {GRID_BASE}/bin/sqlplus",
        f"sudo -iu grid {GRID_BASE}/bin/crsctl check crs",
        *diskgroup_commands,
        f"sudo -iu grid {GRID_BASE}/bin/asmcmd lsdg",
    ]
    return shell_script("Configure ASM diskgroups with ASMLIB", lines)


def _udev_rules(config: AutomationConfig) -> str:
    rules: list[str] = []
    for _label, path, _group, disk in asm_entries(config):
        if not disk.uuid:
            continue
        name = path.rsplit("/", 1)[-1]
        rules.append(
            f'ACTION=="add|change", ENV{{DM_UUID}}=="{disk.dm_uuid}", '
            f'SYMLINK+="oracleasm/{name}", GROUP="{ASM_DEVICE_GROUP}", OWNER="grid", MODE="0660"'
        )
    return "\n".join(rules)
