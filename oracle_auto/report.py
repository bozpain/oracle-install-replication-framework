"""HTML report generation manual.

Reports are local artifacts under `.oracle-auto/reports`. They summarize the
normalized config, generated hostnames, resolver/SCAN decisions, ASM mapping,
and command results captured by the runner. The report writer must never need
SSH access; it only consumes config plus state/result data.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from oracle_auto.automation import StepResult, results_to_html_rows
from oracle_auto.config import AutomationConfig, NodeConfig


def write_html_report(
    config: AutomationConfig,
    results: list[StepResult],
    output_dir: Path,
    title: str | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{_safe_run_id(config.run_id)}.html"
    path.write_text(render_html_report(config, results, title=title), encoding="utf-8")
    return path


def render_html_report(
    config: AutomationConfig,
    results: list[StepResult],
    title: str | None = None,
) -> str:
    page_title = title or f"Oracle Automation Report - {config.run_id}"
    generated_at = datetime.now(timezone.utc).isoformat()
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(page_title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #1f2933; }}
    h1, h2 {{ margin-bottom: 8px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 28px; }}
    th, td {{ border: 1px solid #d9e2ec; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f0f4f8; }}
    code {{ background: #f0f4f8; padding: 2px 4px; border-radius: 3px; }}
    .status-pass {{ color: #0b6b3a; font-weight: 700; }}
    .status-skip {{ color: #52606d; font-weight: 700; }}
    .status-warn {{ color: #a05a00; font-weight: 700; }}
    .status-fail {{ color: #b42318; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>{html.escape(page_title)}</h1>
  <p>Generated at <code>{html.escape(generated_at)}</code></p>
  <h2>Deployment</h2>
  {deployment_table(config)}
  <h2>Topology</h2>
  {topology_table(config)}
  <h2>ASM</h2>
  {asm_table(config)}
  <h2>Installer And Patch</h2>
  {installer_table(config)}
  <h2>Status Summary</h2>
  {summary_table(results)}
  <h2>Execution Results</h2>
  <table>
    <thead>
      <tr><th>Phase</th><th>Host</th><th>Step</th><th>Status</th><th>Message</th></tr>
    </thead>
    <tbody>
      {results_to_html_rows(results) if results else '<tr><td colspan="5">No execution results captured yet.</td></tr>'}
    </tbody>
  </table>
</body>
</html>
"""


def deployment_table(config: AutomationConfig) -> str:
    rows = [
        ("Run ID", config.run_id),
        ("Install Type", config.install_type),
        ("OS", f"{config.version.os_distribution} {config.version.os_version}"),
        ("Oracle Version", config.version.oracle_version),
        ("Oracle Home Version", config.version.oracle_home_version),
        ("Patch Set", config.version.patch_set),
        ("Active Data Guard", "enabled" if config.active_dataguard_enabled else "disabled"),
        ("Data Guard Method", config.dataguard.configuration_method),
        ("Protection Mode", config.dataguard.protection_mode),
        ("DNS Resolvers", ", ".join(config.dns.resolvers)),
        ("NTP Servers", ", ".join(config.os.ntp_servers)),
        ("SELinux", config.os.selinux_mode),
    ]
    return _kv_table(rows)


def topology_table(config: AutomationConfig) -> str:
    rows = []
    for site in config.sites:
        for node in site.nodes:
            rows.append(
                "<tr>"
                f"<td>{html.escape(site.name)}</td>"
                f"<td>{html.escape(site.db_unique_name)}</td>"
                f"<td>{html.escape(site.scan_name or '')}</td>"
                f"<td>{html.escape(node.host)}</td>"
                f"<td>{html.escape(node.public_ip)}</td>"
                f"<td>{html.escape(_private_cell(node))}</td>"
                f"<td>{html.escape(_vip_cell(node))}</td>"
                "</tr>"
            )
    return (
        "<table><thead><tr><th>Site</th><th>DB Unique Name</th><th>SCAN DNS</th>"
        "<th>Public Host</th><th>Public IP</th><th>Private</th><th>VIP</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def asm_table(config: AutomationConfig) -> str:
    rows = [
        ("OCR", _asm_disk_text("OCR", config.asm.ocr_disks)),
        ("DATA", _asm_disk_text("DATA", config.asm.data_disks)),
        ("RECO", _asm_disk_text("RECO", config.asm.reco_disks)),
        ("Redundancy", config.asm.redundancy),
    ]
    return _kv_table(rows)


def installer_table(config: AutomationConfig) -> str:
    patch_text = ", ".join(patch.label for patch in config.installer.patches) or "none"
    rows = [
        ("Sources Path", config.installer.sources_path),
        ("Grid ZIP", config.installer.grid_zip),
        ("Database ZIP", config.installer.db_zip),
        ("OPatch ZIP", config.installer.opatch_zip or ""),
        ("Patch List", patch_text),
    ]
    return _kv_table(rows)


def summary_table(results: list[StepResult]) -> str:
    counts: dict[str, int] = {}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1
    rows = [(status, str(counts.get(status, 0))) for status in ("PASS", "SKIP", "WARN", "FAIL")]
    return _kv_table(rows)


def results_from_state(data: dict[str, Any]) -> list[StepResult]:
    results: list[StepResult] = []
    for step_name, payload in data.get("steps", {}).items():
        details = payload.get("details", {})
        if not details:
            continue
        results.append(
            StepResult(
                phase=str(details.get("phase") or step_name.split(":", 1)[0]),
                host=str(details.get("host", "")),
                name=str(details.get("name") or step_name),
                status=str(details.get("status") or payload.get("status", "")).upper(),
                message=str(details.get("message", "")),
                command=str(details.get("command", "")),
                stdout=str(details.get("stdout", "")),
                stderr=str(details.get("stderr", "")),
                log_path=str(details.get("log_path", "")),
            )
        )
    return results


def _kv_table(rows: list[tuple[str, str]]) -> str:
    body = "\n".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(value)}</td></tr>" for key, value in rows
    )
    return f"<table><tbody>{body}</tbody></table>"


def _private_cell(node: NodeConfig) -> str:
    if not node.private_ip:
        return ""
    return f"{node.private_hostname} / {node.private_ip}"


def _vip_cell(node: NodeConfig) -> str:
    if not node.vip_ip:
        return ""
    return f"{node.vip_hostname} / {node.vip_ip}"


def _asm_disk_text(group: str, disks) -> str:
    values = []
    for index, disk in enumerate(disks, start=1):
        values.append(f"{disk.dm_uuid} -> {disk.symlink_path(group, index)}")
    return ", ".join(values)


def _safe_run_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
