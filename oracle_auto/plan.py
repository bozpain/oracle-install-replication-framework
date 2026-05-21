"""Deployment plan generation manual.

Plan files are local review artifacts. They list every generated phase command
without opening SSH sessions, making DBA and infrastructure review easier than
reading a long terminal dry-run.
"""

from __future__ import annotations

import html
import json
import shlex
from pathlib import Path

from oracle_auto.automation import AutomationStep
from oracle_auto.config import AutomationConfig
from oracle_auto.phase_builders.storage import storage_mapping_text


def write_plan(config: AutomationConfig, steps: list[AutomationStep], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{_safe_run_id(config.run_id)}-plan.html"
    path.write_text(render_plan(config, steps), encoding="utf-8")
    json_path = output_dir / f"{_safe_run_id(config.run_id)}-plan.json"
    json_path.write_text(render_plan_json(config, steps), encoding="utf-8")
    runbook_path = output_dir / f"{_safe_run_id(config.run_id)}-runbook.sh"
    runbook_path.write_text(render_runbook(config, steps), encoding="utf-8")
    _write_phase_runbooks(config, steps, output_dir)
    return path


def render_plan_json(config: AutomationConfig, steps: list[AutomationStep]) -> str:
    payload = {
        "run_id": config.run_id,
        "install_type": config.install_type,
        "steps": [
            {
                "index": index,
                "phase": step.phase,
                "host": step.node.host,
                "name": step.name,
                "title": step.title,
                "timeout": step.timeout,
                "warn_only": step.warn_only,
                "command": step.command,
            }
            for index, step in enumerate(steps, start=1)
        ],
    }
    return json.dumps(payload, indent=2)


def render_plan(config: AutomationConfig, steps: list[AutomationStep]) -> str:
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(index))}</td>"
        f"<td>{html.escape(step.phase)}</td>"
        f"<td>{html.escape(step.node.host)}</td>"
        f"<td>{html.escape(step.name)}</td>"
        f"<td>{html.escape(step.title)}</td>"
        f"<td><pre>{html.escape(step.command)}</pre></td>"
        "</tr>"
        for index, step in enumerate(steps, start=1)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Oracle Automation Plan - {html.escape(config.run_id)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #1f2933; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d9e2ec; padding: 8px; vertical-align: top; }}
    th {{ background: #f0f4f8; }}
    pre {{ white-space: pre-wrap; margin: 0; max-width: 900px; }}
  </style>
</head>
<body>
  <h1>Oracle Automation Plan</h1>
  <p>Run ID: <code>{html.escape(config.run_id)}</code></p>
  <p>Install type: <code>{html.escape(config.install_type)}</code></p>
  <h2>Storage Mapping</h2>
  <pre>{html.escape(storage_mapping_text(config))}</pre>
  <table>
    <thead>
      <tr><th>#</th><th>Phase</th><th>Host</th><th>Step</th><th>Title</th><th>Command</th></tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
"""


def render_runbook(config: AutomationConfig, steps: list[AutomationStep]) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "# oracle-auto generated dry-run artifact.",
        "# Review before manual execution. Generated commands use SSH and target-side root automation.",
        "set -euo pipefail",
        "",
        "# ASM storage mapping",
        "cat <<'MAP'",
        storage_mapping_text(config),
        "MAP",
        "",
    ]
    for index, step in enumerate(steps, start=1):
        user = step.node.ssh_user or config.ssh.user
        ssh_option_parts = [f"-p {config.ssh.port}"]
        if config.ssh.connect_timeout is not None:
            ssh_option_parts.append(f"-o ConnectTimeout={config.ssh.connect_timeout}")
        if config.ssh.server_alive_interval is not None:
            ssh_option_parts.append(f"-o ServerAliveInterval={config.ssh.server_alive_interval}")
        if config.ssh.server_alive_count_max is not None:
            ssh_option_parts.append(f"-o ServerAliveCountMax={config.ssh.server_alive_count_max}")
        ssh_option_parts.append(f"-o StrictHostKeyChecking={config.ssh.strict_host_key_checking}")
        ssh_options = " ".join(ssh_option_parts)
        lines.extend(
            [
                f"# [{index}] {step.phase}:{step.name} on {step.node.host}",
                f"ssh {ssh_options} {shlex.quote(user + '@' + step.node.host)} {shlex.quote(step.command)}",
                "",
            ]
        )
    return "\n".join(lines)


def _write_phase_runbooks(config: AutomationConfig, steps: list[AutomationStep], output_dir: Path) -> None:
    phase_dir = output_dir / f"{_safe_run_id(config.run_id)}-phase-runbooks"
    phase_dir.mkdir(parents=True, exist_ok=True)
    by_phase: dict[str, list[AutomationStep]] = {}
    for step in steps:
        by_phase.setdefault(step.phase, []).append(step)
    for phase, phase_steps in by_phase.items():
        path = phase_dir / f"{_safe_run_id(phase)}.sh"
        path.write_text(render_runbook(config, phase_steps), encoding="utf-8")


def _safe_run_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
