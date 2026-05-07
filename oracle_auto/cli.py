"""CLI manual.

The CLI exposes one command per roadmap phase. Each phase accepts the same
operator controls where possible: `--dry-run`, `--no-resume`, `--json`, and
`--continue-on-fail`. Every executed phase writes or refreshes an HTML report
under `.oracle-auto/reports`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from oracle_auto.doctor import run_doctor
from oracle_auto.automation import AutomationRunner, AutomationStep, StepResult, render_results_text
from oracle_auto.config import AutomationConfig, ConfigError, load_config
from oracle_auto.executor import SSHExecutor
from oracle_auto.phases import (
    analyze_patch_steps,
    apply_db_patch_steps,
    apply_grid_patch_steps,
    apply_patch_steps,
    cleanup_lab_steps,
    collect_diagnostics_steps,
    configure_asm_storage_steps,
    create_database_steps,
    failover_steps,
    install_db_software_steps,
    install_grid_steps,
    inventory_steps,
    datapatch_steps,
    patch_inventory_steps,
    prepare_os_steps,
    prepare_storage_rules_steps,
    prepare_storage_steps,
    setup_active_dataguard_steps,
    setup_dataguard_broker_steps,
    switchover_steps,
    validate_deployment_steps,
    verify_installer_steps,
    update_opatch_steps,
)
from oracle_auto.plan import write_plan
from oracle_auto.precheck import PrecheckRunner
from oracle_auto.report import results_from_state, write_html_report
from oracle_auto.state import NoopStateStore, StateStore


PhaseBuilder = Callable[[AutomationConfig], list[AutomationStep]]


PHASE_BUILDERS: dict[str, PhaseBuilder] = {
    "prepare-os": prepare_os_steps,
    "verify-installer": verify_installer_steps,
    "prepare-storage-rules": prepare_storage_rules_steps,
    "configure-asm-storage": configure_asm_storage_steps,
    "prepare-storage": prepare_storage_steps,
    "install-grid": install_grid_steps,
    "install-db-software": install_db_software_steps,
    "update-opatch": update_opatch_steps,
    "analyze-patch": analyze_patch_steps,
    "apply-grid-patch": apply_grid_patch_steps,
    "apply-db-patch": apply_db_patch_steps,
    "datapatch": datapatch_steps,
    "patch-inventory": patch_inventory_steps,
    "apply-patch": apply_patch_steps,
    "create-database": create_database_steps,
    "setup-active-dataguard": setup_active_dataguard_steps,
    "setup-dataguard-broker": setup_dataguard_broker_steps,
    "validate-deployment": validate_deployment_steps,
    "switchover": switchover_steps,
    "failover": failover_steps,
    "collect-diagnostics": collect_diagnostics_steps,
    "cleanup-lab": cleanup_lab_steps,
    "inventory": inventory_steps,
}


DEPLOYMENT_PHASE_ORDER = [
    "prepare-os",
    "verify-installer",
    "prepare-storage-rules",
    "install-grid",
    "configure-asm-storage",
    "install-db-software",
    "update-opatch",
    "analyze-patch",
    "apply-grid-patch",
    "apply-db-patch",
    "datapatch",
    "patch-inventory",
    "create-database",
    "setup-active-dataguard",
    "setup-dataguard-broker",
    "validate-deployment",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oracle-auto",
        description="Oracle GI, ASM, Database, and Active Data Guard automation framework.",
    )
    parser.add_argument(
        "--state-dir",
        default=".oracle-auto/state",
        help="Directory for resumable execution state files.",
    )
    parser.add_argument(
        "--report-dir",
        default=".oracle-auto/reports",
        help="Directory for generated HTML reports.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-config", help="Validate an automation config file.")
    validate.add_argument("--config", required=True, help="Path to JSON/YAML config.")

    precheck = subparsers.add_parser("precheck", help="Run Oracle installation prechecks.")
    _add_execution_args(precheck)

    for command, help_text in {
        "prepare-os": "Prepare OS users, DNS, hosts, firewall, SELinux, and chrony.",
        "verify-installer": "Verify installer and patch ZIP files on target hosts.",
        "prepare-storage-rules": "Prepare udev rules and /dev/oracleasm symlinks.",
        "configure-asm-storage": "Configure ASMFD labels and OCR/DATA/RECO disk groups.",
        "prepare-storage": "Compatibility wrapper for storage rules and ASM storage.",
        "install-grid": "Install Grid Infrastructure.",
        "install-db-software": "Install Oracle Database software.",
        "update-opatch": "Update OPatch in Grid and Database homes.",
        "analyze-patch": "Analyze configured patches before apply.",
        "apply-grid-patch": "Apply configured patches to Grid home.",
        "apply-db-patch": "Apply configured patches to Database home.",
        "datapatch": "Run datapatch on the primary database home.",
        "patch-inventory": "Collect OPatch inventory.",
        "apply-patch": "Apply OPatch and configured patches.",
        "create-database": "Create the primary database with DBCA silent.",
        "setup-active-dataguard": "Configure and duplicate Active Data Guard standby.",
        "setup-dataguard-broker": "Configure Data Guard Broker when selected.",
        "validate-deployment": "Validate GI, ASM, Database, and Data Guard state.",
        "switchover": "Switchover to standby.",
        "failover": "Failover to standby.",
        "collect-diagnostics": "Collect remote diagnostics for troubleshooting.",
        "cleanup-lab": "Clean limited framework-generated lab artifacts.",
        "inventory": "Collect read-only remote inventory.",
    }.items():
        subparser = subparsers.add_parser(command, help=help_text)
        _add_execution_args(subparser)
        if command == "failover":
            subparser.add_argument(
                "--yes",
                action="store_true",
                help="Confirm failover execution. Required unless --dry-run is used.",
            )
        if command in {"prepare-storage", "prepare-storage-rules", "configure-asm-storage"}:
            subparser.add_argument(
                "--allow-storage-changes",
                action="store_true",
                help="Allow udev/ASM storage changes. Required unless --dry-run is used.",
            )
        if command in {"apply-patch", "update-opatch", "analyze-patch", "apply-grid-patch", "apply-db-patch", "datapatch"}:
            subparser.add_argument(
                "--allow-patch-apply",
                action="store_true",
                help="Allow OPatch, patch analysis/apply, or datapatch. Required unless --dry-run is used.",
            )
        if command == "cleanup-lab":
            subparser.add_argument(
                "--yes",
                action="store_true",
                help="Confirm limited lab cleanup. Required unless --dry-run is used.",
            )

    report = subparsers.add_parser("generate-report", help="Generate HTML report from current state.")
    report.add_argument("--config", required=True, help="Path to JSON/YAML config.")

    plan = subparsers.add_parser("generate-plan", help="Generate an HTML execution plan without SSH.")
    plan.add_argument("--config", required=True, help="Path to JSON/YAML config.")
    plan.add_argument(
        "--phases",
        nargs="*",
        default=DEPLOYMENT_PHASE_ORDER,
        help="Optional phase command names to include. Defaults to all deployment phases.",
    )

    doctor = subparsers.add_parser("doctor", help="Run local control-machine readiness checks.")
    doctor.add_argument("--config", required=True, help="Path to JSON/YAML config.")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable JSON result.")

    return parser


def _add_execution_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True, help="Path to JSON/YAML config.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned remote commands without opening SSH sessions.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore completed step state and run all checks/steps again.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON result.",
    )
    parser.add_argument(
        "--continue-on-fail",
        action="store_true",
        help="Continue remaining steps after a failure.",
    )
    parser.add_argument(
        "--log-dir",
        default=".oracle-auto/logs",
        help="Directory for per-step stdout/stderr log artifacts.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate-config":
        _print_config_summary(config)
        return 0

    if args.command == "doctor":
        items = run_doctor(config, Path(args.report_dir), Path(args.state_dir), Path(".oracle-auto/logs"))
        if args.json:
            print(json.dumps([item.to_dict() for item in items], indent=2))
        else:
            for item in items:
                print(f"{item.status:5} {item.name:24} {item.message}")
        return 1 if any(item.status == "FAIL" for item in items) else 0

    state = StateStore(Path(args.state_dir), config.run_id)

    if args.command == "generate-report":
        results = results_from_state(state.data)
        path = write_html_report(config, results, Path(args.report_dir))
        print(f"Report written: {path}")
        return 0

    if args.command == "generate-plan":
        steps = _build_plan_steps(config, args.phases)
        path = write_plan(config, steps, Path(args.report_dir))
        print(f"Plan written: {path}")
        return 0

    if args.command == "failover" and not args.dry_run and not getattr(args, "yes", False):
        print("Failover requires --yes unless --dry-run is used.", file=sys.stderr)
        return 2
    if args.command == "cleanup-lab" and not args.dry_run and not getattr(args, "yes", False):
        print("cleanup-lab requires --yes unless --dry-run is used.", file=sys.stderr)
        return 2
    if args.command in {"prepare-storage", "prepare-storage-rules", "configure-asm-storage"} and not args.dry_run:
        if not getattr(args, "allow_storage_changes", False):
            print(f"{args.command} requires --allow-storage-changes unless --dry-run is used.", file=sys.stderr)
            return 2
    if args.command in {"apply-patch", "update-opatch", "analyze-patch", "apply-grid-patch", "apply-db-patch", "datapatch"} and not args.dry_run and not getattr(args, "allow_patch_apply", False):
        print(f"{args.command} requires --allow-patch-apply unless --dry-run is used.", file=sys.stderr)
        return 2

    if args.command == "precheck":
        return _run_precheck(args, config)

    builder = PHASE_BUILDERS.get(args.command)
    if builder is None:
        parser.error(f"Unknown command: {args.command}")
        return 2

    return _run_phase(args, config, builder(config))


def _run_precheck(args, config: AutomationConfig) -> int:
    state = NoopStateStore() if args.dry_run else StateStore(Path(args.state_dir), config.run_id)
    executor = SSHExecutor(config.ssh, dry_run=args.dry_run)
    runner = PrecheckRunner(config, executor, state=state, resume=not args.no_resume)
    results = runner.run()
    step_results = [
        StepResult(
            phase="precheck",
            host=item.host,
            name=item.name,
            status=item.status,
            message=item.message,
            command=item.command,
            stdout=item.stdout,
            stderr=item.stderr,
        )
        for item in results
    ]
    step_results = _write_precheck_logs(config, step_results, Path(args.log_dir))
    _print_or_json(args, step_results)
    report = write_html_report(config, step_results, Path(args.report_dir), title=f"Oracle Precheck - {config.run_id}")
    print(f"\nReport written: {report}")
    return 1 if any(item.status == "FAIL" for item in step_results) else 0


def _run_phase(args, config: AutomationConfig, steps: list[AutomationStep]) -> int:
    if not steps:
        print(f"No steps generated for command: {args.command}")
        return 0

    state = NoopStateStore() if args.dry_run else StateStore(Path(args.state_dir), config.run_id)
    executor = SSHExecutor(config.ssh, dry_run=args.dry_run)
    runner = AutomationRunner(
        executor,
        state=state,
        resume=not args.no_resume,
        continue_on_fail=args.continue_on_fail,
        log_dir=Path(args.log_dir) / config.run_id,
    )
    results = runner.run(steps)
    _print_or_json(args, results)
    report = write_html_report(config, results, Path(args.report_dir), title=f"{args.command} - {config.run_id}")
    print(f"\nReport written: {report}")
    return 1 if any(item.status == "FAIL" for item in results) else 0


def _print_or_json(args, results: list[StepResult]) -> None:
    if args.json:
        print(json.dumps([item.to_dict() for item in results], indent=2))
    else:
        print(render_results_text(results))


def _write_precheck_logs(config: AutomationConfig, results: list[StepResult], log_dir: Path) -> list[StepResult]:
    updated: list[StepResult] = []
    for item in results:
        host_dir = log_dir / config.run_id / "precheck" / _safe_filename(item.host)
        host_dir.mkdir(parents=True, exist_ok=True)
        path = host_dir / f"{_safe_filename(item.name)}.log"
        path.write_text(
            "\n".join(
                [
                    f"phase={item.phase}",
                    f"host={item.host}",
                    f"step={item.name}",
                    f"status={item.status}",
                    f"command={item.command}",
                    "",
                    "STDOUT:",
                    item.stdout,
                    "",
                    "STDERR:",
                    item.stderr,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        updated.append(
            StepResult(
                phase=item.phase,
                host=item.host,
                name=item.name,
                status=item.status,
                message=item.message,
                command=item.command,
                stdout=item.stdout,
                stderr=item.stderr,
                log_path=str(path),
            )
        )
    return updated


def _safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)


def _build_plan_steps(config: AutomationConfig, phase_names: list[str]) -> list[AutomationStep]:
    steps: list[AutomationStep] = []
    for name in phase_names:
        builder = PHASE_BUILDERS.get(name)
        if builder is None:
            raise ConfigError(f"Unknown phase for plan: {name}")
        steps.extend(builder(config))
    return steps


def _print_config_summary(config: AutomationConfig) -> None:
    print("Config valid.")
    print(f"Install type       : {config.install_type}")
    print(f"Primary site       : {config.primary_site.name} ({len(config.primary_site.nodes)} node(s))")
    print(f"ASM diskgroups     : OCR={len(config.asm.ocr_disks)}, DATA={len(config.asm.data_disks)}, RECO={len(config.asm.reco_disks)}")
    print(f"DNS resolvers      : {', '.join(config.dns.resolvers)}")
    print(f"Installer path     : {config.installer.sources_path}")
    print(f"Patch set          : {config.version.patch_set}")
    if config.active_dataguard_enabled and config.standby_site:
        print(f"Standby site       : {config.standby_site.name} ({len(config.standby_site.nodes)} node(s))")
        print(f"Active Data Guard  : enabled ({config.dataguard.configuration_method}, max_performance)")
    else:
        print("Active Data Guard  : disabled")
