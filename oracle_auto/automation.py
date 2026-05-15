"""Automation runner manual.

Every install command in this project is modeled as a list of `AutomationStep`
objects. A step has a phase name, a target host, and one shell script. The
runner is responsible for dry-run display, SSH execution, state/resume, and
normalizing results for the HTML report.

Operational rule: phase builders should stay declarative. Put command intent in
the step title and keep risky shell details inside idempotent scripts.
"""

from __future__ import annotations

import html
import shlex
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from oracle_auto.config import NodeConfig
from oracle_auto.executor import CommandResult, SSHExecutor
from oracle_auto.secrets import redact
from oracle_auto.state import StateBackend


@dataclass(frozen=True)
class AutomationStep:
    phase: str
    name: str
    node: NodeConfig
    command: str
    title: str
    timeout: int | None = 600
    warn_only: bool = False

    @property
    def state_key(self) -> str:
        return f"{self.phase}:{self.node.host}:{self.name}"


@dataclass(frozen=True)
class StepResult:
    phase: str
    host: str
    name: str
    status: str
    message: str
    command: str
    stdout: str = ""
    stderr: str = ""
    log_path: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "phase": self.phase,
            "host": self.host,
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "log_path": self.log_path,
        }


class AutomationRunner:
    def __init__(
        self,
        executor: SSHExecutor,
        state: StateBackend,
        resume: bool = True,
        continue_on_fail: bool = False,
        log_dir: Path | None = None,
        parallel_by_host: bool = False,
    ):
        self.executor = executor
        self.state = state
        self.resume = resume
        self.continue_on_fail = continue_on_fail
        self.log_dir = log_dir
        self.parallel_by_host = parallel_by_host

    def run(self, steps: list[AutomationStep]) -> list[StepResult]:
        if self.parallel_by_host and len({step.node.host for step in steps}) > 1:
            return self._run_parallel_by_host(steps)
        return self._run_serial(steps)

    def _run_serial(self, steps: list[AutomationStep]) -> list[StepResult]:
        results: list[StepResult] = []
        for step in steps:
            result = self._run_one(step)
            results.append(result)

            if result.status == "FAIL":
                if not self.continue_on_fail:
                    break

        return results

    def _run_parallel_by_host(self, steps: list[AutomationStep]) -> list[StepResult]:
        grouped: dict[str, list[tuple[int, AutomationStep]]] = defaultdict(list)
        for index, step in enumerate(steps):
            grouped[step.node.host].append((index, step))

        stop_event = Event()

        def run_host_chain(items: list[tuple[int, AutomationStep]]) -> list[tuple[int, StepResult]]:
            results: list[tuple[int, StepResult]] = []
            for index, step in items:
                if stop_event.is_set() and not self.continue_on_fail:
                    break
                result = self._run_one(step)
                results.append((index, result))
                if result.status == "FAIL" and not self.continue_on_fail:
                    stop_event.set()
                    break
            return results

        indexed_results: list[tuple[int, StepResult]] = []
        with ThreadPoolExecutor(max_workers=len(grouped)) as pool:
            for host_results in pool.map(run_host_chain, grouped.values()):
                indexed_results.extend(host_results)
        return [result for _index, result in sorted(indexed_results, key=lambda item: item[0])]

    def _run_one(self, step: AutomationStep) -> StepResult:
        if self.resume and self.state.is_done(step.state_key):
            return StepResult(
                phase=step.phase,
                host=step.node.host,
                name=step.name,
                status="PASS",
                message="Already completed; use --no-resume to re-run.",
                command=step.command,
            )

        print(f"RUN   {step.phase}:{step.node.host}:{step.name}  {step.title}", flush=True)
        self.state.mark_running(step.state_key)
        command_result = self.executor.run(step.node, step.command, timeout=step.timeout)
        result = self._to_step_result(step, command_result)
        result = self._with_log_path(step, result)
        if result.status == "FAIL":
            self.state.mark_failed(step.state_key, result.to_dict())
        else:
            self.state.mark_done(step.state_key, result.to_dict())
        return result

    def _with_log_path(self, step: AutomationStep, result: StepResult) -> StepResult:
        if self.log_dir is None:
            return result
        host_dir = self.log_dir / step.phase / _safe_filename(step.node.host)
        host_dir.mkdir(parents=True, exist_ok=True)
        path = host_dir / f"{_safe_filename(step.name)}.log"
        path.write_text(
            "\n".join(
                [
                    f"phase={result.phase}",
                    f"host={result.host}",
                    f"step={result.name}",
                    f"status={result.status}",
                    f"command={result.command}",
                    "",
                    "STDOUT:",
                    result.stdout,
                    "",
                    "STDERR:",
                    result.stderr,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return StepResult(
            phase=result.phase,
            host=result.host,
            name=result.name,
            status=result.status,
            message=result.message,
            command=result.command,
            stdout=result.stdout,
            stderr=result.stderr,
            log_path=str(path),
        )

    @staticmethod
    def _to_step_result(step: AutomationStep, result: CommandResult) -> StepResult:
        if result.ok:
            status = "DRYRUN" if result.skipped else "PASS"
            message = result.command if result.skipped else (_compact(result.stdout) or "OK")
        elif step.warn_only:
            status = "WARN"
            message = f"{step.title} returned non-zero status."
        else:
            status = "FAIL"
            message = f"{step.title} failed."

        detail = result.stderr if result.stderr else result.stdout
        if not result.ok and detail:
            message = f"{message} ({_compact(detail)})"

        return StepResult(
            phase=step.phase,
            host=result.host,
            name=step.name,
            status=status,
            message=redact(message),
            command=redact(result.command),
            stdout=redact(result.stdout),
            stderr=redact(result.stderr),
        )


def shell_script(title: str, lines: list[str]) -> str:
    body = "\n".join(lines)
    script = f"""# oracle-auto: {title}
# Manual: generated by the Oracle install replication framework.
# Manual: run through sudo unless the command explicitly switches to grid/oracle.
set -euo pipefail
if test -r /etc/oracle-auto/secrets.env; then
  set -a
  . /etc/oracle-auto/secrets.env
  set +a
fi
echo "==> {title}"
{body}
"""
    return "sudo -n bash -lc " + shlex.quote(script)


def render_results_text(results: list[StepResult]) -> str:
    current_phase = None
    current_host = None
    chunks: list[str] = []
    for item in results:
        if item.phase != current_phase:
            current_phase = item.phase
            current_host = None
            chunks.append(f"\n## {item.phase}")
        if item.host != current_host:
            current_host = item.host
            chunks.append(f"\n[{item.host}]")
        chunks.append(f"{item.status:5} {item.name:32} {item.message}")
    return "\n".join(chunks).strip()


def results_to_html_rows(results: list[StepResult]) -> str:
    rows = []
    for item in results:
        rows.append(
            "<tr>"
            f"<td>{html.escape(item.phase)}</td>"
            f"<td>{html.escape(item.host)}</td>"
            f"<td>{html.escape(item.name)}</td>"
            f"<td class=\"status-{html.escape(item.status.lower())}\">{html.escape(item.status)}</td>"
            f"<td>{html.escape(item.message)}{_log_suffix(item)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _compact(value: str, limit: int = 180) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _log_suffix(item: StepResult) -> str:
    if not item.log_path:
        return ""
    return f"<br><code>{html.escape(item.log_path)}</code>"


def _safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)
