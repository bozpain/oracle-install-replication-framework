"""ASM storage phase manual.

Builds UUID-driven udev rules first, then performs ASMFD labeling and diskgroup
creation in a separate post-GI phase. The old `prepare-storage` command remains
as a compatibility wrapper for dry-run review.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import ASMDiskConfig, AutomationConfig
from oracle_auto.phase_builders.common import GRID_BASE, make_step


ASMEntry = tuple[str, str, str, ASMDiskConfig]


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
            "Configure ASM Filter Driver labels and disk groups",
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


def create_diskgroup_sql(name: str, labels: list[str], redundancy: str) -> str:
    disk_list = ",".join(f"'AFD:{label}'" for label in labels)
    return (
        f"sudo -iu grid {GRID_BASE}/bin/sqlplus -s / as sysasm <<'SQL'\n"
        "WHENEVER SQLERROR EXIT SQL.SQLCODE\n"
        f"DECLARE\n  existing NUMBER;\nBEGIN\n  SELECT COUNT(*) INTO existing FROM v$asm_diskgroup WHERE name = '{name}';\n"
        "  IF existing = 0 THEN\n"
        f"    EXECUTE IMMEDIATE q'[CREATE DISKGROUP {name} {redundancy} REDUNDANCY DISK {disk_list} ATTRIBUTE 'compatible.asm'='19.0','compatible.rdbms'='19.0','compatible.advm'='19.0']';\n"
        "  END IF;\nEND;\n/\nSQL"
    )


def afd_label_command(label: str, path: str, *, initial: bool = False) -> str:
    quoted_label = shlex.quote(label)
    quoted_path = shlex.quote(path)
    label_args = f"{quoted_label} {quoted_path}"
    if initial:
        label_args = f"{label_args} --init"
    label_check = (
        f"{GRID_BASE}/bin/asmcmd afd_lslbl {quoted_path} 2>/dev/null | "
        f"awk '{{print $1}}' | grep -qx {quoted_label}"
    )
    return (
        f"if {label_check}; then "
        f"echo 'AFD label already exists: {label}'; "
        "else "
        f"{GRID_BASE}/bin/asmcmd afd_label {label_args}; "
        "fi; "
        f"{GRID_BASE}/bin/asmcmd afd_lslbl {quoted_path}; "
        f"{GRID_BASE}/bin/asmcmd afd_lslbl {quoted_path} 2>/dev/null | "
        f"awk '{{print $1}}' | grep -qx {quoted_label}"
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
            f"chown -h grid:asmadmin {shlex.quote(path)} && "
            f"chown grid:asmadmin \"$(readlink -f {shlex.quote(source)})\" && "
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
    lines = [
        "command -v udevadm",
        "echo 'Planned ASM disk mapping:'",
        "cat <<'MAP'\n" + storage_mapping_text(config) + "\nMAP",
        *uuid_checks,
        *path_checks,
        "mkdir -p /dev/oracleasm",
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
        *disk_checks,
        "ls -l /dev/oracleasm",
    ]
    return shell_script("Prepare ASM udev rules", lines)


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
    label_commands = [afd_label_command(label, path) for label, path, _group, _disk in entries]
    diskgroup_commands = []
    for diskgroup_name in ("OCR", "DATA", "RECO"):
        labels = [label for label, _path, group, _disk in entries if group == diskgroup_name]
        if labels:
            diskgroup_commands.append(create_diskgroup_sql(diskgroup_name, labels, config.asm.redundancy))
    lines = [
        "echo 'Resolved ASM disk mapping before AFD label:'",
        "for path in " + " ".join(shlex.quote(path) for _label, path, _group, _disk in entries) + "; do printf '%s -> ' \"$path\"; readlink -f \"$path\"; done",
        *disk_checks,
        *signature_checks,
        *size_checks,
        f"test -x {GRID_BASE}/bin/asmcmd",
        f"test -x {GRID_BASE}/bin/sqlplus",
        f"sudo -iu grid {GRID_BASE}/bin/crsctl check crs",
        f"sudo -iu grid {GRID_BASE}/bin/asmcmd afd_state || true",
        *label_commands,
        f"sudo -iu grid {GRID_BASE}/bin/asmcmd afd_lslbl || true",
        *diskgroup_commands,
        f"sudo -iu grid {GRID_BASE}/bin/asmcmd lsdg",
    ]
    return shell_script("Configure ASM AFD labels and diskgroups", lines)


def _udev_rules(config: AutomationConfig) -> str:
    rules: list[str] = []
    for _label, path, _group, disk in asm_entries(config):
        if not disk.uuid:
            continue
        name = path.rsplit("/", 1)[-1]
        rules.append(
            f'ACTION=="add|change", ENV{{DM_UUID}}=="{disk.dm_uuid}", '
            f'SYMLINK+="oracleasm/{name}", GROUP="asmadmin", OWNER="grid", MODE="0660"'
        )
    return "\n".join(rules)
