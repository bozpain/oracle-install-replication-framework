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

from oracle_auto.automation import AutomationRunner, AutomationStep, StepResult, render_results_text
from oracle_auto.config import AutomationConfig, ConfigError, load_config
from oracle_auto.executor import SSHExecutor
from oracle_auto.phases import (
    apply_patch_steps,
    create_database_steps,
    failover_steps,
    install_db_software_steps,
    install_grid_steps,
    prepare_os_steps,
    prepare_storage_steps,
    setup_active_dataguard_steps,
    setup_dataguard_broker_steps,
    switchover_steps,
    validate_deployment_steps,
    verify_installer_steps,
)
from oracle_auto.precheck import PrecheckRunner
from oracle_auto.report import results_from_state, write_html_report
from oracle_auto.state import NoopStateStore, StateStore


PhaseBuilder = Callable[[AutomationConfig], list[AutomationStep]]


PHASE_BUILDERS: dict[str, PhaseBuilder] = {
    "prepare-os": prepare_os_steps,
    "verify-installer": verify_installer_steps,
    "prepare-storage": prepare_storage_steps,
    "install-grid": install_grid_steps,
    "install-db-software": install_db_software_steps,
    "apply-patch": apply_patch_steps,
    "create-database": create_database_steps,
    "setup-active-dataguard": setup_active_dataguard_steps,
    "setup-dataguard-broker": setup_dataguard_broker_steps,
    "validate-deployment": validate_deployment_steps,
    "switchover": switchover_steps,
    "failover": failover_steps,
}


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
        "prepare-storage": "Prepare ASM AFD labels and OCR/DATA/RECO disk groups.",
        "install-grid": "Install Grid Infrastructure.",
        "install-db-software": "Install Oracle Database software.",
        "apply-patch": "Apply OPatch and configured patches.",
        "create-database": "Create the primary database with DBCA silent.",
        "setup-active-dataguard": "Configure and duplicate Active Data Guard standby.",
        "setup-dataguard-broker": "Configure Data Guard Broker when selected.",
        "validate-deployment": "Validate GI, ASM, Database, and Data Guard state.",
        "switchover": "Switchover to standby.",
        "failover": "Failover to standby.",
    }.items():
        subparser = subparsers.add_parser(command, help=help_text)
        _add_execution_args(subparser)
        if command == "failover":
            subparser.add_argument(
                "--yes",
                action="store_true",
                help="Confirm failover execution. Required unless --dry-run is used.",
            )

    report = subparsers.add_parser("generate-report", help="Generate HTML report from current state.")
    report.add_argument("--config", required=True, help="Path to JSON/YAML config.")

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

    state = StateStore(Path(args.state_dir), config.run_id)

    if args.command == "generate-report":
        results = results_from_state(state.data)
        path = write_html_report(config, results, Path(args.report_dir))
        print(f"Report written: {path}")
        return 0

    if args.command == "failover" and not args.dry_run and not getattr(args, "yes", False):
        print("Failover requires --yes unless --dry-run is used.", file=sys.stderr)
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
