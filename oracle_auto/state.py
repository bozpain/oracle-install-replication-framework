"""State store manual.

State files make long Oracle automation resumable. Each step writes running,
done, or failed status under `.oracle-auto/state/<run_id>.json`, and operators
can re-run with `--no-resume` when a phase must be forced.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, state_dir: Path, run_id: str):
        self.state_dir = state_dir
        self.run_id = _safe_run_id(run_id)
        self.path = self.state_dir / f"{self.run_id}.json"
        self.data = self._load()

    def is_done(self, step: str) -> bool:
        return self.data.get("steps", {}).get(step, {}).get("status") == "done"

    def mark_running(self, step: str) -> None:
        self._mark(step, "running")

    def mark_done(self, step: str, details: dict[str, Any] | None = None) -> None:
        self._mark(step, "done", details)

    def mark_failed(self, step: str, details: dict[str, Any] | None = None) -> None:
        self._mark(step, "failed", details)

    def _mark(self, step: str, status: str, details: dict[str, Any] | None = None) -> None:
        steps = self.data.setdefault("steps", {})
        steps[step] = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "details": details or {},
        }
        self._save()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"run_id": self.run_id, "steps": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


def _safe_run_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)


class NoopStateStore:
    def is_done(self, step: str) -> bool:
        return False

    def mark_running(self, step: str) -> None:
        return None

    def mark_done(self, step: str, details: dict[str, Any] | None = None) -> None:
        return None

    def mark_failed(self, step: str, details: dict[str, Any] | None = None) -> None:
        return None
