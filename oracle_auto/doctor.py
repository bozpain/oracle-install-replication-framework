"""Local doctor checks manual.

Doctor runs only on the control machine. It validates local prerequisites and
configuration parseability before any remote SSH execution is attempted.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from oracle_auto.config import AutomationConfig


@dataclass(frozen=True)
class DoctorItem:
    name: str
    status: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "message": self.message}


def run_doctor(config: AutomationConfig, report_dir: Path, state_dir: Path, log_dir: Path) -> list[DoctorItem]:
    items = [
        _python_version_check(),
        _binary_check("ssh"),
        _writable_dir_check("report_dir", report_dir),
        _writable_dir_check("state_dir", state_dir),
        _writable_dir_check("log_dir", log_dir),
        DoctorItem("config", "PASS", f"Config parsed for run_id={config.run_id}, install_type={config.install_type}."),
        DoctorItem("sample_yaml_support", "PASS" if _yaml_available() else "WARN", "PyYAML available." if _yaml_available() else "PyYAML not installed; JSON configs still work."),
    ]
    return items


def _python_version_check() -> DoctorItem:
    version = sys.version_info
    if version.major == 3 and version.minor == 12:
        return DoctorItem("python_version", "PASS", sys.version.split()[0])
    return DoctorItem("python_version", "WARN", f"Expected Python 3.12, got {sys.version.split()[0]}.")


def _binary_check(name: str) -> DoctorItem:
    path = shutil.which(name)
    if path:
        return DoctorItem(f"binary_{name}", "PASS", path)
    return DoctorItem(f"binary_{name}", "FAIL", f"Required binary not found: {name}")


def _writable_dir_check(name: str, path: Path) -> DoctorItem:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".oracle-auto-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return DoctorItem(name, "FAIL", str(exc))
    return DoctorItem(name, "PASS", str(path))


def _yaml_available() -> bool:
    try:
        import yaml  # type: ignore  # noqa: F401
    except ImportError:
        return False
    return True
