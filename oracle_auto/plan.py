"""Deployment plan generation manual.

Plan files are local review artifacts. They list every generated phase command
without opening SSH sessions, making DBA and infrastructure review easier than
reading a long terminal dry-run.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from oracle_auto.automation import AutomationStep
from oracle_auto.config import AutomationConfig


def write_plan(config: AutomationConfig, steps: list[AutomationStep], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{_safe_run_id(config.run_id)}-plan.html"
    path.write_text(render_plan(config, steps), encoding="utf-8")
    json_path = output_dir / f"{_safe_run_id(config.run_id)}-plan.json"
    json_path.write_text(render_plan_json(config, steps), encoding="utf-8")
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
  <table>
    <thead>
      <tr><th>#</th><th>Phase</th><th>Host</th><th>Step</th><th>Title</th><th>Command</th></tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
"""


def _safe_run_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
