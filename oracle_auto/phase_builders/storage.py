"""ASM storage phase manual.

Builds UUID-driven udev rules for `/dev/oracleasm/*`, reloads udev, validates
stable symlinks, labels devices with ASMFD, and creates `OCR`, `DATA`, and
`RECO` diskgroups.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import ASMDiskConfig, AutomationConfig
from oracle_auto.phase_builders.common import make_step


ASMEntry = tuple[str, str, str, ASMDiskConfig]


def prepare_storage_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        make_step(
            "prepare-storage",
            "prepare_asm_storage",
            node,
            "Prepare ASM Filter Driver labels and disk groups",
            _prepare_storage_script(config),
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
            entries.append((label, disk.symlink_path(group, index), group, disk))
    return entries


def create_diskgroup_sql(name: str, labels: list[str], redundancy: str) -> str:
    disk_list = ",".join(f"'AFD:{label}'" for label in labels)
    return (
        "sudo -iu grid sqlplus -s / as sysasm <<'SQL'\n"
        "WHENEVER SQLERROR EXIT SQL.SQLCODE\n"
        f"DECLARE\n  existing NUMBER;\nBEGIN\n  SELECT COUNT(*) INTO existing FROM v$asm_diskgroup WHERE name = '{name}';\n"
        "  IF existing = 0 THEN\n"
        f"    EXECUTE IMMEDIATE q'[CREATE DISKGROUP {name} {redundancy} REDUNDANCY DISK {disk_list} ATTRIBUTE 'compatible.asm'='19.0','compatible.rdbms'='19.0','compatible.advm'='19.0']';\n"
        "  END IF;\nEND;\n/\nSQL"
    )


def _prepare_storage_script(config: AutomationConfig) -> str:
    entries = asm_entries(config)
    rules = _udev_rules(config)
    disk_checks = [f"test -b {shlex.quote(path)}" for _label, path, _group, _disk in entries]
    label_commands = [
        f"asmcmd afd_label {label} {shlex.quote(path)} --init || asmcmd afd_label {label} {shlex.quote(path)}"
        for label, path, _group, _disk in entries
    ]
    diskgroup_commands = [
        create_diskgroup_sql("OCR", [label for label, _path, group, _disk in entries if group == "OCR"], config.asm.redundancy),
        create_diskgroup_sql("DATA", [label for label, _path, group, _disk in entries if group == "DATA"], config.asm.redundancy),
        create_diskgroup_sql("RECO", [label for label, _path, group, _disk in entries if group == "RECO"], config.asm.redundancy),
    ]
    lines = [
        "mkdir -p /dev/oracleasm",
        "cat > /etc/udev/rules.d/99-oracleasm.rules <<'EOF'\n" + rules + "\nEOF",
        "udevadm control --reload-rules",
        "udevadm trigger --subsystem-match=block --action=change",
        "udevadm settle",
        *disk_checks,
        "ls -l /dev/oracleasm",
        "command -v asmcmd",
        "asmcmd afd_state || true",
        *label_commands,
        "asmcmd afd_lslbl || true",
        *diskgroup_commands,
        "sudo -iu grid asmcmd lsdg",
    ]
    return shell_script("Prepare ASM AFD labels and diskgroups", lines)


def _udev_rules(config: AutomationConfig) -> str:
    rules: list[str] = []
    for _label, path, _group, disk in asm_entries(config):
        name = path.rsplit("/", 1)[-1]
        rules.append(
            f'ACTION=="add|change", ENV{{DM_UUID}}=="{disk.dm_uuid}", '
            f'SYMLINK+="oracleasm/{name}", GROUP="asmadmin", OWNER="grid", MODE="0660"'
        )
    return "\n".join(rules)

