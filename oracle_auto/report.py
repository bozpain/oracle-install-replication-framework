"""HTML report generation manual.

Reports are local artifacts under `.oracle-auto/reports`. They summarize the
normalized config, generated hostnames, resolver/SCAN decisions, ASM mapping,
and command results captured by the runner. The report writer must never need
SSH access; it only consumes config plus state/result data.
"""

from __future__ import annotations

import html
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from oracle_auto.automation import StepResult, results_to_html_rows
from oracle_auto.config import AutomationConfig, NodeConfig
from oracle_auto.phase_builders.storage import asm_entries


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


def publish_html_report(report_path: Path, publish_path: Path, url_base: str | None = None) -> tuple[Path, str | None]:
    publish_path.mkdir(parents=True, exist_ok=True)
    target = publish_path / report_path.name
    shutil.copy2(report_path, target)
    url = f"{url_base.rstrip('/')}/{target.name}" if url_base else None
    return target, url


def render_html_report(
    config: AutomationConfig,
    results: list[StepResult],
    title: str | None = None,
) -> str:
    page_title = title or f"Oracle Automation Report - {config.run_id}"
    generated_at = datetime.now(timezone.utc).isoformat()
    counts = _status_counts(results)
    total_steps = sum(counts.values())
    validation = _validation_results(results)
    health = _operational_health(validation, results)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(page_title)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #090d14;
      --panel: #111827;
      --panel-2: #162033;
      --line: #28364f;
      --line-soft: rgba(148, 163, 184, 0.18);
      --text: #eef4ff;
      --muted: #9fb0c8;
      --muted-2: #6f819d;
      --cyan: #38bdf8;
      --blue: #60a5fa;
      --green: #34d399;
      --lime: #a3e635;
      --yellow: #fbbf24;
      --orange: #fb923c;
      --red: #fb7185;
      --violet: #a78bfa;
      --pink: #f472b6;
      --shadow: 0 24px 70px rgba(0, 0, 0, 0.42);
    }}
    * {{ box-sizing: border-box; }}
    html {{ background: var(--bg); }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      font: 14px/1.55 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 12% 8%, rgba(56, 189, 248, 0.20), transparent 28rem),
        radial-gradient(circle at 88% 4%, rgba(244, 114, 182, 0.14), transparent 30rem),
        linear-gradient(135deg, #070b12 0%, #0b1020 52%, #111827 100%);
    }}
    .shell {{ width: min(1560px, calc(100% - 40px)); margin: 0 auto; padding: 28px 0 48px; }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) auto;
      gap: 24px;
      align-items: end;
      padding: 30px;
      border: 1px solid var(--line-soft);
      border-radius: 18px;
      background: linear-gradient(135deg, rgba(17, 24, 39, 0.96), rgba(22, 32, 51, 0.86));
      box-shadow: var(--shadow);
    }}
    h1 {{ margin: 0; font-size: clamp(28px, 3vw, 46px); line-height: 1.06; letter-spacing: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 18px; letter-spacing: 0; }}
    .eyebrow {{ color: var(--cyan); font-size: 12px; font-weight: 800; letter-spacing: 0.14em; text-transform: uppercase; }}
    .hero-meta {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; color: var(--muted); }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 32px;
      padding: 6px 11px;
      border: 1px solid var(--line-soft);
      border-radius: 999px;
      background: rgba(15, 23, 42, 0.72);
      color: var(--muted);
      font-weight: 700;
    }}
    .hero-score {{
      min-width: 220px;
      padding: 20px;
      border-radius: 14px;
      border: 1px solid rgba(52, 211, 153, 0.32);
      background: linear-gradient(160deg, rgba(52, 211, 153, 0.18), rgba(96, 165, 250, 0.10));
    }}
    .hero-score strong {{ display: block; font-size: 34px; line-height: 1; margin-bottom: 7px; }}
    .hero-score span {{ color: var(--muted); font-weight: 700; }}
    .metrics {{ display: grid; grid-template-columns: repeat(5, minmax(130px, 1fr)); gap: 12px; margin: 18px 0 24px; }}
    .metric {{
      padding: 16px;
      border-radius: 14px;
      border: 1px solid var(--line-soft);
      background: rgba(15, 23, 42, 0.72);
      box-shadow: 0 18px 46px rgba(0, 0, 0, 0.22);
    }}
    .metric b {{ display: block; font-size: 28px; line-height: 1; margin-bottom: 8px; }}
    .metric span {{ color: var(--muted); font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.08em; }}
    .metric-pass b {{ color: var(--green); }}
    .metric-warn b {{ color: var(--yellow); }}
    .metric-fail b {{ color: var(--red); }}
    .metric-dryrun b, .metric-skip b {{ color: var(--blue); }}
    .readiness {{ display: grid; grid-template-columns: repeat(5, minmax(150px, 1fr)); gap: 12px; margin: 18px 0 24px; }}
    .readiness-card {{
      padding: 16px;
      border-radius: 14px;
      border: 1px solid var(--line-soft);
      background: linear-gradient(160deg, rgba(15, 23, 42, 0.92), rgba(30, 41, 59, 0.72));
      min-height: 118px;
    }}
    .readiness-card strong {{ display: block; color: var(--text); font-size: 16px; margin: 8px 0 5px; }}
    .readiness-card span {{ color: var(--muted); font-size: 13px; }}
    .dot {{ width: 10px; height: 10px; display: inline-block; border-radius: 50%; background: var(--muted-2); box-shadow: 0 0 18px currentColor; }}
    .ready-pass .dot {{ background: var(--green); color: var(--green); }}
    .ready-warn .dot {{ background: var(--yellow); color: var(--yellow); }}
    .ready-fail .dot {{ background: var(--red); color: var(--red); }}
    .ready-info .dot {{ background: var(--blue); color: var(--blue); }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }}
    .wide {{ grid-column: 1 / -1; }}
    section {{
      min-width: 0;
      padding: 20px;
      border-radius: 16px;
      border: 1px solid var(--line-soft);
      background: rgba(15, 23, 42, 0.84);
      box-shadow: 0 18px 54px rgba(0, 0, 0, 0.24);
    }}
    table {{ border-collapse: separate; border-spacing: 0; width: 100%; overflow: hidden; border: 1px solid var(--line-soft); border-radius: 12px; }}
    th, td {{ padding: 11px 12px; text-align: left; vertical-align: top; border-bottom: 1px solid var(--line-soft); }}
    th {{ color: #dbeafe; background: rgba(30, 41, 59, 0.96); font-size: 12px; text-transform: uppercase; letter-spacing: 0.07em; }}
    td {{ color: #e5eefc; background: rgba(2, 6, 23, 0.28); }}
    tbody tr:nth-child(even) td {{ background: rgba(15, 23, 42, 0.56); }}
    tr:last-child th, tr:last-child td {{ border-bottom: 0; }}
    .kv th {{ width: 235px; color: var(--muted); }}
    .status-pass, .status-dryrun, .status-skip, .status-warn, .status-fail {{ font-weight: 900; }}
    td.status-pass, .badge-pass {{ color: var(--green); }}
    td.status-warn, .badge-warn {{ color: var(--yellow); }}
    td.status-fail, .badge-fail {{ color: var(--red); }}
    td.status-dryrun, td.status-skip, .badge-dryrun, .badge-skip {{ color: var(--blue); }}
    .badge {{
      display: inline-flex;
      min-width: 78px;
      justify-content: center;
      padding: 5px 10px;
      border-radius: 999px;
      border: 1px solid currentColor;
      background: rgba(255, 255, 255, 0.06);
      font-size: 12px;
      font-weight: 900;
      letter-spacing: 0.04em;
    }}
    code {{
      color: #bfdbfe;
      background: rgba(96, 165, 250, 0.13);
      border: 1px solid rgba(96, 165, 250, 0.22);
      padding: 2px 6px;
      border-radius: 6px;
    }}
    p {{ color: var(--muted); margin: 0; }}
    .empty {{ color: var(--muted); padding: 12px 0 2px; }}
    .result-table td:nth-child(5) {{ color: #dbeafe; }}
    .result-table td:nth-child(3), .result-table td:nth-child(5) {{ overflow-wrap: anywhere; }}
    .evidence-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .evidence {{
      min-width: 0;
      border: 1px solid var(--line-soft);
      border-radius: 14px;
      overflow: hidden;
      background: rgba(2, 6, 23, 0.30);
    }}
    .evidence h3 {{
      margin: 0;
      padding: 13px 15px;
      font-size: 15px;
      background: rgba(30, 41, 59, 0.90);
      border-bottom: 1px solid var(--line-soft);
    }}
    .evidence h3 span {{ color: var(--muted); font-weight: 700; }}
    .evidence-block {{ padding: 13px 15px; border-bottom: 1px solid var(--line-soft); }}
    .evidence-block:last-child {{ border-bottom: 0; }}
    .evidence-title {{ color: var(--cyan); font-size: 12px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }}
    pre {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      margin: 0;
      color: #dbeafe;
      font: 12px/1.5 "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
    }}
    @media (max-width: 980px) {{
      .hero, .grid {{ grid-template-columns: 1fr; }}
      .metrics, .readiness, .evidence-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .shell {{ width: min(100% - 22px, 1560px); padding-top: 12px; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <header class="hero">
      <div>
        <div class="eyebrow">Oracle Automation Report</div>
        <h1>{html.escape(page_title)}</h1>
        <div class="hero-meta">
          <span class="pill">Run <code>{html.escape(config.run_id)}</code></span>
          <span class="pill">{html.escape(config.install_type.upper())}</span>
          <span class="pill">Generated <code>{html.escape(generated_at)}</code></span>
        </div>
      </div>
      <div class="hero-score">
        <strong>{html.escape(health)}</strong>
        <span>{total_steps} automation steps captured as implementation evidence</span>
      </div>
    </header>
    {readiness_cards(validation)}
    <div class="grid">
      <section class="wide">
        <h2>Operational Readiness Evidence</h2>
        {validation_evidence_panel(validation)}
      </section>
      <section>
        <h2>Deployment</h2>
        {deployment_table(config)}
      </section>
      <section>
        <h2>Data Guard</h2>
        {dataguard_table(config)}
      </section>
      <section class="wide">
        <h2>Topology</h2>
        {topology_table(config)}
      </section>
      <section>
        <h2>SCAN DNS</h2>
        {scan_table(config)}
      </section>
      <section>
        <h2>Installer And Patch</h2>
        {installer_table(config)}
      </section>
      <section class="wide">
        <h2>ASM Disk Mapping</h2>
        {asm_table(config)}
      </section>
      <section class="wide">
        <h2>Automation Audit Trail</h2>
        <table class="result-table">
          <thead>
            <tr><th>Phase</th><th>Host</th><th>Step</th><th>Status</th><th>Message</th></tr>
          </thead>
          <tbody>
            {results_to_html_rows(results) if results else '<tr><td colspan="5">No execution results captured yet.</td></tr>'}
          </tbody>
        </table>
      </section>
    </div>
  </main>
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
        ("Data Guard Method", config.dataguard.configuration_method or "not selected"),
        ("Protection Mode", config.dataguard.protection_mode),
        ("DNS Resolvers", ", ".join(config.dns.resolvers)),
        ("DNS Model", "SCAN only; public/private/VIP are managed in /etc/hosts"),
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
    rows = []
    for site in config.sites:
        for label, path, group, disk in asm_entries(config, site):
            rows.append(
                "<tr>"
                f"<td>{html.escape(site.name)}</td>"
                f"<td>{html.escape(group)}</td>"
                f"<td>{html.escape(label)}</td>"
                f"<td>{html.escape(disk.source_for(site_name=site.name))}</td>"
                f"<td>{html.escape(path)}</td>"
                f"<td>{html.escape(config.asm.redundancy)}</td>"
                "</tr>"
            )
    return (
        "<table><thead><tr><th>Site</th><th>Diskgroup</th><th>ASMLIB Label</th><th>Source</th><th>Device Path</th><th>Redundancy</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def scan_table(config: AutomationConfig) -> str:
    rows = []
    for site in config.sites:
        rows.append(
            "<tr>"
            f"<td>{html.escape(site.name)}</td>"
            f"<td>{html.escape(site.scan_name or 'not used')}</td>"
            f"<td>{html.escape('DNS required' if site.scan_name else 'not required')}</td>"
            "</tr>"
        )
    return "<table><thead><tr><th>Site</th><th>SCAN Name</th><th>Validation Source</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def dataguard_table(config: AutomationConfig) -> str:
    rows = [
        ("Enabled", "yes" if config.active_dataguard_enabled else "no"),
        ("Method", config.dataguard.configuration_method or "not selected"),
        ("Protection Mode", config.dataguard.protection_mode),
        ("Primary DB Unique Name", config.primary_site.db_unique_name),
        ("Standby DB Unique Name", config.standby_site.db_unique_name if config.standby_site else ""),
    ]
    return _kv_table(rows)


def installer_table(config: AutomationConfig) -> str:
    rows = [
        ("Sources Path", config.installer.sources_path),
        ("Grid ZIP", config.installer.grid_zip),
        ("Database ZIP", config.installer.db_zip),
        ("ASMLIB RPMs", ", ".join(f"{arch}: {rpm}" for arch, rpm in sorted(config.os.asmlib_rpms.items()))),
        ("OPatch ZIP", config.installer.opatch_zip or ""),
        ("Grid Patch", _patch_text(config.installer.grid_patch)),
        ("Database Patch", _patch_text(config.installer.db_patch)),
        ("OJVM Patch", _patch_text(config.installer.ojvm_patch)),
    ]
    return _kv_table(rows)


def summary_table(results: list[StepResult]) -> str:
    counts = _status_counts(results)
    rows = [(status, str(counts.get(status, 0))) for status in ("PASS", "DRYRUN", "SKIP", "WARN", "FAIL")]
    return _kv_table(rows)


def readiness_cards(validation: list[StepResult]) -> str:
    checks = [
        (
            "Primary Open",
            "Primary is available for application workload",
            _evidence_state(validation, "PASS: primary database is open read/write", "primary database is not open"),
        ),
        (
            "Standby Apply",
            "Managed recovery is active and applying redo",
            _evidence_state(validation, "PASS: managed recovery process", "managed recovery process is not visible"),
        ),
        (
            "Redo Transport",
            "Primary has a valid physical standby destination",
            _evidence_state(validation, "PASS: primary has a valid physical standby", "no VALID physical standby"),
        ),
        (
            "Archive Gap",
            "Standby reports no archive gap",
            _evidence_state(validation, "PASS: no archive gap", "archive gap rows exist"),
        ),
        (
            "Switchover",
            "Primary status supports planned switchover",
            _evidence_state(validation, "PASS: primary switchover status", "review primary switchover"),
        ),
    ]
    cards = []
    for title, subtitle, state in checks:
        cards.append(
            f'<div class="readiness-card ready-{html.escape(state)}">'
            '<i class="dot"></i>'
            f"<strong>{html.escape(title)}</strong>"
            f"<span>{html.escape(subtitle)}</span>"
            "</div>"
        )
    return f'<div class="readiness">{"".join(cards)}</div>'


def validation_evidence_panel(validation: list[StepResult]) -> str:
    if not validation:
        return '<p class="empty">Validate Deployment evidence has not been captured yet.</p>'

    cards = []
    for item in validation:
        title = _validation_title(item)
        sections = _selected_validation_sections(item)
        if not sections:
            sections = [("Summary", item.message or "Validation step completed.")]
        blocks = []
        for section_title, text in sections:
            blocks.append(
                '<div class="evidence-block">'
                f'<div class="evidence-title">{html.escape(section_title)}</div>'
                f"<pre>{html.escape(_trim_evidence(text))}</pre>"
                "</div>"
            )
        cards.append(
            '<article class="evidence">'
            f"<h3>{html.escape(title)} <span>{html.escape(item.host)}</span></h3>"
            f"{''.join(blocks)}"
            "</article>"
        )
    return f'<div class="evidence-grid">{"".join(cards)}</div>'


def status_cards(counts: dict[str, int]) -> str:
    cards = []
    for status in ("PASS", "WARN", "FAIL", "DRYRUN", "SKIP"):
        cards.append(
            f'<div class="metric metric-{html.escape(status.lower())}">'
            f"<b>{counts.get(status, 0)}</b>"
            f"<span>{html.escape(status)}</span>"
            "</div>"
        )
    return f'<div class="metrics">{"".join(cards)}</div>'


def failed_steps_table(results: list[StepResult]) -> str:
    failed = [item for item in results if item.status == "FAIL"]
    if not failed:
        return '<p class="empty">No failed steps captured.</p>'
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(item.phase)}</td>"
        f"<td>{html.escape(item.host)}</td>"
        f"<td>{html.escape(item.name)}</td>"
        f"<td>{_status_badge(item.status)}</td>"
        f"<td>{html.escape(item.message)}</td>"
        f"<td>{html.escape(item.log_path)}</td>"
        "</tr>"
        for item in failed
    )
    return (
        "<table><thead><tr><th>Phase</th><th>Host</th><th>Step</th><th>Status</th><th>Message</th><th>Log</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


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


def _kv_table(rows: list[tuple[str, Any]]) -> str:
    body = "\n".join(
        f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(_display_value(value))}</td></tr>" for key, value in rows
    )
    return f'<table class="kv"><tbody>{body}</tbody></table>'


def _status_counts(results: list[StepResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        status = item.status.upper()
        counts[status] = counts.get(status, 0) + 1
    return counts


def _validation_results(results: list[StepResult]) -> list[StepResult]:
    wanted = {
        "validate_grid_asm",
        "validate_primary_database",
        "validate_standby_database",
    }
    return [item for item in results if item.name in wanted]


def _validation_title(item: StepResult) -> str:
    titles = {
        "validate_grid_asm": "Grid Infrastructure And ASM",
        "validate_primary_database": "Primary Database Readiness",
        "validate_standby_database": "Standby Database Replication",
    }
    return titles.get(item.name, item.name.replace("_", " ").title())


def _selected_validation_sections(item: StepResult) -> list[tuple[str, str]]:
    if item.name == "validate_grid_asm":
        return _grid_asm_evidence(item.stdout)
    sections = _sqlplus_sections(item.stdout)
    wanted = _wanted_section_names(item.name)
    selected: list[tuple[str, str]] = []
    for name in wanted:
        text = sections.get(name)
        if text:
            selected.append((name.title(), text))
    readiness = _readiness_lines(item.stdout)
    if readiness:
        selected.insert(0, ("Readiness Summary", "\n".join(readiness)))
    return selected


def _wanted_section_names(step_name: str) -> tuple[str, ...]:
    if step_name == "validate_primary_database":
        return (
            "LISTENER READINESS FOR ORCL",
            "PRIMARY DATABASE IDENTITY AND ROLE",
            "PRIMARY DATA GUARD TRANSPORT DESTINATIONS",
            "PRIMARY ARCHIVE LOG MODE",
        )
    if step_name == "validate_standby_database":
        return (
            "LISTENER READINESS FOR ORCLSTBY",
            "STANDBY DATABASE IDENTITY AND ROLE",
            "STANDBY DATA GUARD LAG",
            "STANDBY ARCHIVE GAP",
            "STANDBY RECEIVED REDO SEQUENCE",
            "STANDBY APPLIED REDO SEQUENCE",
            "STANDBY MANAGED RECOVERY PROCESSES",
        )
    return ()


def _sqlplus_sections(stdout: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        match = re.fullmatch(r"=+\s+(.+?)\s+=+", line)
        if match:
            if current and buffer:
                sections[current] = "\n".join(buffer).strip()
            current = match.group(1).strip().upper()
            buffer = []
            continue
        if current is not None:
            buffer.append(raw_line.rstrip())
    if current and buffer:
        sections[current] = "\n".join(buffer).strip()
    return sections


def _readiness_lines(stdout: str) -> list[str]:
    lines = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if line.startswith(("PASS:", "WARN:", "INFO:", "FAIL:")):
            lines.append(line)
    return lines


def _grid_asm_evidence(stdout: str) -> list[tuple[str, str]]:
    important = []
    for raw_line in stdout.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if (
            stripped.startswith("CRS-4638")
            or "ora.asm" in stripped
            or "ora.DATA.dg" in stripped
            or "ora.RECO.dg" in stripped
            or stripped.endswith(" DATA/")
            or stripped.endswith(" RECO/")
        ):
            important.append(line)
    if important:
        return [("Cluster And ASM Storage", "\n".join(important))]
    return []


def _trim_evidence(text: str, limit: int = 2200) -> str:
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 80].rstrip() + "\n... evidence trimmed; see automation audit trail for full step log ..."


def _evidence_state(validation: list[StepResult], pass_text: str, warn_text: str) -> str:
    combined = "\n".join(item.stdout for item in validation)
    if pass_text in combined:
        return "pass"
    if warn_text in combined or "FAIL:" in combined:
        return "warn"
    return "info"


def _operational_health(validation: list[StepResult], results: list[StepResult]) -> str:
    if validation:
        states = {
            _evidence_state(validation, "PASS: primary database is open read/write", "primary database is not open"),
            _evidence_state(validation, "PASS: managed recovery process", "managed recovery process is not visible"),
            _evidence_state(validation, "PASS: primary has a valid physical standby", "no VALID physical standby"),
            _evidence_state(validation, "PASS: no archive gap", "archive gap rows exist"),
        }
        if "warn" in states:
            return "REVIEW"
        if states == {"pass"}:
            return "READY"
        return "CHECK"
    if not results:
        return "READY"
    return "RECORDED"


def _status_badge(status: str) -> str:
    normalized = status.lower()
    return f'<span class="badge badge-{html.escape(normalized)}">{html.escape(status.upper())}</span>'


def _display_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _private_cell(node: NodeConfig) -> str:
    if not node.private_ip:
        return ""
    return f"{node.private_hostname} / {node.private_ip}"


def _vip_cell(node: NodeConfig) -> str:
    if not node.vip_ip:
        return ""
    return f"{node.vip_hostname} / {node.vip_ip}"


def _patch_text(patch: Any) -> str:
    if patch is None:
        return "none"
    return f"{patch.label} ({patch.file})"


def _safe_run_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
