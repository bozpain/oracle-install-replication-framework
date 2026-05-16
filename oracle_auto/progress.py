"""Lightweight progress heartbeat for long-running executions."""

from __future__ import annotations

import json
import threading
from pathlib import Path


def render_progress_line(state_path: Path) -> str:
    state = _load_state(state_path)
    steps = state.get("steps", {})

    counts = {"done": 0, "warning": 0, "failed": 0, "running": 0}
    current: list[str] = []
    for step_name, step in steps.items():
        status = str(step.get("status") or "")
        if status in counts:
            counts[status] += 1
        if status == "running":
            current.append(step_name)

    current_text = ", ".join(current) if current else "-"
    return (
        f"[PROGRESS] done={counts['done']} warning={counts['warning']} failed={counts['failed']} "
        f"running={counts['running']} seen={len(steps)} | current={current_text}"
    )


def start_progress(state_path: Path, interval: int = 10) -> tuple[threading.Event, threading.Thread]:
    stop_event = threading.Event()

    def worker() -> None:
        while not stop_event.is_set():
            print(render_progress_line(state_path), flush=True)
            stop_event.wait(interval)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return stop_event, thread


def stop_progress(state_path: Path, stop_event: threading.Event | None, thread: threading.Thread | None) -> None:
    if stop_event is None:
        return
    stop_event.set()
    if thread is not None:
        thread.join(timeout=2)
    print(render_progress_line(state_path), flush=True)


def _load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
