"""CLI manual.

The CLI exposes one command per roadmap phase. Each phase accepts the same
operator controls where possible: `--dry-run`, `--no-resume`, `--json`, and
`--continue-on-fail`. Every executed phase writes or refreshes an HTML report
under `.oracle-auto/reports`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Callable

from oracle_auto.doctor import run_doctor
from oracle_auto.automation import AutomationRunner, AutomationStep, StepResult, render_results_text
from oracle_auto.config import AutomationConfig, ConfigError, VALID_ASM_STORAGE_MODES, load_config
from oracle_auto.executor import SSHExecutor
from oracle_auto.phases import (
    analyze_patch_steps,
    apply_db_patch_steps,
    apply_grid_patch_steps,
    apply_ojvm_patch_steps,
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
    configure_dataguard_steps,
    switchover_steps,
    validate_deployment_steps,
    verify_installer_steps,
    update_opatch_steps,
)
from oracle_auto.plan import write_plan
from oracle_auto.precheck import PrecheckRunner
from oracle_auto.progress import start_progress, stop_progress
from oracle_auto.report import publish_html_report, results_from_state, write_html_report
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
    "apply-ojvm-patch": apply_ojvm_patch_steps,
    "datapatch": datapatch_steps,
    "patch-inventory": patch_inventory_steps,
    "apply-patch": apply_patch_steps,
    "create-database": create_database_steps,
    "configure-dataguard": configure_dataguard_steps,
    "validate-deployment": validate_deployment_steps,
    "switchover": switchover_steps,
    "failover": failover_steps,
    "collect-diagnostics": collect_diagnostics_steps,
    "cleanup-lab": cleanup_lab_steps,
    "rollback-framework": cleanup_lab_steps,
    "inventory": inventory_steps,
}


WORKFLOW_PHASE_ORDER = [
    "precheck",
    "prepare-os",
    "verify-installer",
    "prepare-storage-rules",
    "install-grid",
    "configure-asm-storage",
    "install-db-software",
    "apply-ojvm-patch",
    "create-database",
    "patch-inventory",
    "configure-dataguard",
    "validate-deployment",
]


ASM_STORAGE_MODE_CHOICES = sorted({*VALID_ASM_STORAGE_MODES, "asmlib", "raw_udev"})
DATAGUARD_MODE_CHOICES = ["manual", "broker"]

DEPLOYMENT_PHASE_ORDER = [
    "prepare-os",
    "verify-installer",
    "prepare-storage-rules",
    "install-grid",
    "configure-asm-storage",
    "install-db-software",
    "apply-ojvm-patch",
    "create-database",
    "patch-inventory",
    "configure-dataguard",
    "validate-deployment",
]


PARALLEL_HOST_PHASES = {
    "prepare-os",
    "verify-installer",
    "prepare-storage-rules",
    "install-grid",
    "configure-asm-storage",
    "install-db-software",
    "update-opatch",
    "apply-ojvm-patch",
    "patch-inventory",
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
    _add_asm_storage_mode_arg(validate)
    _add_dataguard_mode_arg(validate)

    precheck = subparsers.add_parser("precheck", help="Run Oracle installation prechecks.")
    _add_execution_args(precheck)

    for command, help_text in {
        "full": "Run precheck and the full install plus replication workflow.",
        "resume": "Resume the full workflow using the existing state file.",
    }.items():
        subparser = subparsers.add_parser(command, help=help_text)
        _add_execution_args(subparser)
        _add_workflow_args(subparser)

    for command, help_text in {
        "prepare-os": "Prepare OS users, DNS, hosts, firewall, SELinux, and chrony.",
        "verify-installer": "Verify installer ZIP, patch ZIP, and ASMLIB RPM files on target hosts.",
        "prepare-storage-rules": "Prepare persistent device paths and ASMLIB labels.",
        "configure-asm-storage": "Configure ASMLIB disks and ASM disk groups.",
        "prepare-storage": "Compatibility wrapper for storage rules and ASM storage.",
        "install-grid": "Install Grid Infrastructure.",
        "install-db-software": "Install Oracle Database software.",
        "update-opatch": "Update OPatch in Grid and Database homes.",
        "analyze-patch": "Analyze configured Grid and Database patches.",
        "apply-grid-patch": "Apply the configured Grid patch to Grid home.",
        "apply-db-patch": "Apply the configured Database patch to Database home.",
        "apply-ojvm-patch": "Apply the configured OJVM patch to Database home before database creation.",
        "datapatch": "Run datapatch on the primary database home.",
        "patch-inventory": "Collect Oracle home version summary.",
        "apply-patch": "Apply OPatch and configured patches.",
        "create-database": "Create the primary database with DBCA silent.",
        "configure-dataguard": "Configure Data Guard standby and Broker when selected.",
        "validate-deployment": "Validate GI, ASM, Database, and Data Guard state.",
        "switchover": "Switchover to standby.",
        "failover": "Failover to standby.",
        "collect-diagnostics": "Collect remote diagnostics for troubleshooting.",
        "cleanup-lab": "Clean limited framework-generated lab artifacts.",
        "rollback-framework": "Rollback limited framework-generated files without removing Oracle homes/databases.",
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
                help="Allow ASMLIB/ASM storage changes. Required unless --dry-run is used.",
            )
        if command in {"apply-patch", "update-opatch", "analyze-patch", "apply-grid-patch", "apply-db-patch", "apply-ojvm-patch", "datapatch"}:
            subparser.add_argument(
                "--allow-patch-apply",
                action="store_true",
                help="Allow OPatch, patch analysis/apply, or datapatch. Required unless --dry-run is used.",
            )
        if command in {"cleanup-lab", "rollback-framework"}:
            subparser.add_argument(
                "--yes",
                action="store_true",
                help="Confirm limited framework cleanup/rollback. Required unless --dry-run is used.",
            )

    report = subparsers.add_parser("generate-report", help="Generate HTML report from current state.")
    report.add_argument("--config", required=True, help="Path to JSON/YAML config.")
    _add_asm_storage_mode_arg(report)
    _add_dataguard_mode_arg(report)

    plan = subparsers.add_parser("generate-plan", help="Generate an HTML execution plan without SSH.")
    plan.add_argument("--config", required=True, help="Path to JSON/YAML config.")
    _add_asm_storage_mode_arg(plan)
    _add_dataguard_mode_arg(plan)
    plan.add_argument(
        "--phases",
        nargs="*",
        default=DEPLOYMENT_PHASE_ORDER,
        help="Optional phase command names to include. Defaults to all deployment phases.",
    )

    doctor = subparsers.add_parser("doctor", help="Run local control-machine readiness checks.")
    doctor.add_argument("--config", required=True, help="Path to JSON/YAML config.")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable JSON result.")
    _add_asm_storage_mode_arg(doctor)
    _add_dataguard_mode_arg(doctor)

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
    _add_asm_storage_mode_arg(parser)
    _add_dataguard_mode_arg(parser)


def _add_asm_storage_mode_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--asm-storage-mode",
        choices=ASM_STORAGE_MODE_CHOICES,
        help="Override asm.storage_mode for this run. Choices: raw, asmlibv3, afd.",
    )


def _add_dataguard_mode_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dataguard-mode",
        choices=DATAGUARD_MODE_CHOICES,
        help="Select Data Guard mode for standby deployments. Choices: manual, broker.",
    )


def _add_workflow_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--from-phase",
        choices=WORKFLOW_PHASE_ORDER,
        help="Start the workflow at this phase. Earlier phases are not executed.",
    )
    parser.add_argument(
        "--to-phase",
        choices=WORKFLOW_PHASE_ORDER,
        help="Stop the workflow after this phase.",
    )
    parser.add_argument(
        "--allow-storage-changes",
        action="store_true",
        help="Allow ASMLIB/ASM storage changes in workflow phases. Required unless --dry-run is used.",
    )
    parser.add_argument(
        "--allow-patch-apply",
        action="store_true",
        help="Allow OPatch, patch analysis/apply, or datapatch in workflow phases. Required unless --dry-run is used.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    config = _with_asm_storage_mode_override(args, config)
    config = _with_dataguard_mode_override(args, config)
    try:
        _ensure_dataguard_mode_selected(args, config)
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
    if _is_execution_command(args.command) and not args.dry_run:
        try:
            _validate_or_record_run_context(args, config, state)
        except ConfigError as exc:
            print(f"Config error: {exc}", file=sys.stderr)
            return 2

    if args.command == "generate-report":
        print("RUN   generate-report:local:generate_report  Generate HTML report from current state", flush=True)
        results = results_from_state(state.data)
        path = write_html_report(config, results, Path(args.report_dir))
        _print_report_location(config, path)
        return 0

    if args.command == "generate-plan":
        steps = _build_plan_steps(config, args.phases)
        path = write_plan(config, steps, Path(args.report_dir))
        print(f"Plan written: {path}")
        return 0

    if args.command == "failover" and not args.dry_run and not getattr(args, "yes", False):
        print("Failover requires --yes unless --dry-run is used.", file=sys.stderr)
        return 2
    if args.command in {"cleanup-lab", "rollback-framework"} and not args.dry_run and not getattr(args, "yes", False):
        print(f"{args.command} requires --yes unless --dry-run is used.", file=sys.stderr)
        return 2
    if args.command in {"prepare-storage", "prepare-storage-rules", "configure-asm-storage"} and not args.dry_run:
        if not getattr(args, "allow_storage_changes", False):
            print(f"{args.command} requires --allow-storage-changes unless --dry-run is used.", file=sys.stderr)
            return 2
    if args.command in {"apply-patch", "update-opatch", "analyze-patch", "apply-grid-patch", "apply-db-patch", "apply-ojvm-patch", "datapatch"} and not args.dry_run and not getattr(args, "allow_patch_apply", False):
        print(f"{args.command} requires --allow-patch-apply unless --dry-run is used.", file=sys.stderr)
        return 2

    if args.command in {"full", "resume"}:
        return _run_workflow(args, config)

    if args.command == "precheck":
        return _run_precheck(args, config)

    builder = PHASE_BUILDERS.get(args.command)
    if builder is None:
        parser.error(f"Unknown command: {args.command}")
        return 2

    return _run_phase(args, config, builder(config))


def _run_precheck(args, config: AutomationConfig) -> int:
    progress = _start_progress_if_needed(args, config)
    try:
        step_results = _execute_precheck(args, config)
    finally:
        _stop_progress_if_needed(args, config, progress)
    _print_or_json(args, step_results)
    report = write_html_report(config, step_results, Path(args.report_dir), title=f"Oracle Precheck - {config.run_id}")
    _print_report_location(config, report, prefix="\n")
    return 1 if any(item.status == "FAIL" for item in step_results) else 0


def _run_phase(args, config: AutomationConfig, steps: list[AutomationStep]) -> int:
    if not steps:
        print(f"No steps generated for command: {args.command}")
        return 0

    progress = _start_progress_if_needed(args, config)
    try:
        results = _execute_phase(args, config, steps)
    finally:
        _stop_progress_if_needed(args, config, progress)
    _print_or_json(args, results)
    report = write_html_report(config, results, Path(args.report_dir), title=f"{args.command} - {config.run_id}")
    _print_report_location(config, report, prefix="\n")
    return 1 if any(item.status == "FAIL" for item in results) else 0


def _run_workflow(args, config: AutomationConfig) -> int:
    try:
        phases = _selected_workflow_phases(args.from_phase, args.to_phase)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    if not args.dry_run:
        if any(phase in {"prepare-storage-rules", "configure-asm-storage"} for phase in phases) and not args.allow_storage_changes:
            print(f"{args.command} requires --allow-storage-changes unless --dry-run is used.", file=sys.stderr)
            return 2
        if any(phase in {"update-opatch", "apply-ojvm-patch"} for phase in phases) and not args.allow_patch_apply:
            print(f"{args.command} requires --allow-patch-apply unless --dry-run is used.", file=sys.stderr)
            return 2

    all_results: list[StepResult] = []
    progress = _start_progress_if_needed(args, config)
    try:
        for phase in phases:
            print(f"\n== Workflow phase: {phase} ==")
            if phase == "precheck":
                results = _execute_precheck(args, config)
            else:
                builder = PHASE_BUILDERS[phase]
                phase_args = argparse.Namespace(**vars(args))
                phase_args.command = phase
                results = _execute_phase(phase_args, config, builder(config))
            all_results.extend(results)
            _print_or_json(args, results)
            if any(item.status == "FAIL" for item in results) and not args.continue_on_fail:
                break
    finally:
        _stop_progress_if_needed(args, config, progress)

    report = write_html_report(config, all_results, Path(args.report_dir), title=f"{args.command} - {config.run_id}")
    _print_report_location(config, report, prefix="\n")
    return 1 if any(item.status == "FAIL" for item in all_results) else 0


def _print_report_location(config: AutomationConfig, report: Path, prefix: str = "") -> None:
    print(f"{prefix}Report written: {report}")
    publish_path, url_base = _report_publish_settings(config)
    if not publish_path:
        return
    try:
        target, url = publish_html_report(report, Path(publish_path), url_base)
    except OSError as exc:
        print(f"WARN  report publish failed: {exc}", file=sys.stderr)
        return
    print(f"Report published: {target}")
    print(f"REPORT_HTML={target}")
    if url:
        print(f"Report URL: {url}")
        print(f"REPORT_URL={url}")


def _report_publish_settings(config: AutomationConfig) -> tuple[str | None, str | None]:
    path = config.report_publish.path or os.environ.get("ORACLE_AUTO_REPORT_PUBLISH_PATH")
    url_base = config.report_publish.url_base or os.environ.get("ORACLE_AUTO_REPORT_URL_BASE")
    return path, url_base


def _execute_precheck(args, config: AutomationConfig) -> list[StepResult]:
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
    return _write_precheck_logs(config, step_results, Path(args.log_dir))


def _execute_phase(args, config: AutomationConfig, steps: list[AutomationStep]) -> list[StepResult]:
    state = NoopStateStore() if args.dry_run else StateStore(Path(args.state_dir), config.run_id)
    executor = SSHExecutor(config.ssh, dry_run=args.dry_run)
    executable_steps = _with_remote_resume_override(steps) if args.no_resume else steps
    runner = AutomationRunner(
        executor,
        state=state,
        resume=not args.no_resume,
        continue_on_fail=args.continue_on_fail,
        log_dir=Path(args.log_dir) / config.run_id,
        parallel_by_host=args.command in PARALLEL_HOST_PHASES,
    )
    return runner.run(executable_steps)


def _with_remote_resume_override(steps: list[AutomationStep]) -> list[AutomationStep]:
    return [
        replace(step, command=f"ORACLE_AUTO_NO_REMOTE_RESUME=1 {step.command}")
        for step in steps
    ]


def _start_progress_if_needed(args, config: AutomationConfig):
    if args.dry_run:
        return None
    state_path = StateStore(Path(args.state_dir), config.run_id).path
    return start_progress(state_path)


def _stop_progress_if_needed(args, config: AutomationConfig, progress) -> None:
    if progress is None:
        return
    state_path = StateStore(Path(args.state_dir), config.run_id).path
    stop_event, thread = progress
    stop_progress(state_path, stop_event, thread)


def _selected_workflow_phases(from_phase: str | None, to_phase: str | None) -> list[str]:
    start = WORKFLOW_PHASE_ORDER.index(from_phase) if from_phase else 0
    end = WORKFLOW_PHASE_ORDER.index(to_phase) if to_phase else len(WORKFLOW_PHASE_ORDER) - 1
    if start > end:
        raise ConfigError("--from-phase must not come after --to-phase.")
    return WORKFLOW_PHASE_ORDER[start : end + 1]


def _with_asm_storage_mode_override(args, config: AutomationConfig) -> AutomationConfig:
    mode = getattr(args, "asm_storage_mode", None)
    if not mode:
        return config
    if mode == "asmlib":
        mode = "asmlibv3"
    if mode == "raw_udev":
        mode = "raw"
    return replace(config, asm=replace(config.asm, storage_mode=mode))


def _with_dataguard_mode_override(args, config: AutomationConfig) -> AutomationConfig:
    mode = getattr(args, "dataguard_mode", None)
    if not mode:
        return config
    return replace(config, dataguard=replace(config.dataguard, configuration_method=mode))


def _ensure_dataguard_mode_selected(args, config: AutomationConfig) -> None:
    if (
        config.standby_site
        and config.dataguard.configuration_method is None
        and _command_requires_dataguard_mode(args)
    ):
        raise ConfigError("Data Guard mode must be supplied with --dataguard-mode for Data Guard actions.")


def _command_requires_dataguard_mode(args) -> bool:
    if args.command in {"configure-dataguard", "switchover", "failover"}:
        return True
    if args.command in {"full", "resume"}:
        phases = _selected_workflow_phases(args.from_phase, args.to_phase)
        return "configure-dataguard" in phases
    return False


def _is_execution_command(command: str) -> bool:
    return command in {"precheck", "full", "resume", *PHASE_BUILDERS}


def _validate_or_record_run_context(args, config: AutomationConfig, state: StateStore) -> None:
    context = state.data.setdefault("context", {})
    previous = context.get("asm_storage_mode")
    if previous == "asmlib":
        previous = "asmlibv3"
    if previous == "raw_udev":
        previous = "raw"
    current = config.asm.storage_mode
    if previous and previous != current and not args.no_resume:
        raise ConfigError(
            f"Resume storage mode mismatch. Previous run used {previous}, current run uses {current}. "
            "Use the same --asm-storage-mode or rerun with --no-resume for a new execution."
        )
    context["asm_storage_mode"] = current
    state._save()


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
    if config.asm.ocr_disks:
        asm_summary = f"OCR={len(config.asm.ocr_disks)}, DATA={len(config.asm.data_disks)}, RECO={len(config.asm.reco_disks)}"
    else:
        asm_summary = f"DATA={len(config.asm.data_disks)}, RECO={len(config.asm.reco_disks)}"
    print(f"ASM diskgroups     : {asm_summary}")
    print(f"DNS resolvers      : {', '.join(config.dns.resolvers)}")
    scans = ", ".join(site.scan_name for site in config.sites if site.scan_name) or "not used"
    print(f"SCAN DNS           : {scans}")
    print("Public/priv/VIP DNS: ignored; managed via /etc/hosts")
    print(f"Installer path     : {config.installer.sources_path}")
    print(f"Patch set          : {config.version.patch_set}")
    if config.active_dataguard_enabled and config.standby_site:
        method = config.dataguard.configuration_method or "not selected"
        print(f"Standby site       : {config.standby_site.name} ({len(config.standby_site.nodes)} node(s))")
        print(f"Active Data Guard  : enabled ({method}, max_performance)")
    else:
        print("Active Data Guard  : disabled")
