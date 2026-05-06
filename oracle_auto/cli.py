import argparse
import json
import sys
from pathlib import Path

from oracle_auto.config import ConfigError, load_config
from oracle_auto.executor import SSHExecutor
from oracle_auto.precheck import PrecheckRunner
from oracle_auto.state import NoopStateStore, StateStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oracle-auto",
        description="Oracle installation and Data Guard automation framework.",
    )
    parser.add_argument(
        "--state-dir",
        default=".oracle-auto/state",
        help="Directory for resumable execution state files.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-config", help="Validate an automation config file.")
    validate.add_argument("--config", required=True, help="Path to JSON/YAML config.")

    precheck = subparsers.add_parser("precheck", help="Run Oracle installation prechecks.")
    precheck.add_argument("--config", required=True, help="Path to JSON/YAML config.")
    precheck.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned remote commands without opening SSH sessions.",
    )
    precheck.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore completed step state and run all checks again.",
    )
    precheck.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON result.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate-config":
        print("Config valid.")
        print(f"Install type : {config.install_type}")
        print(f"Primary site : {config.primary_site.name} ({len(config.primary_site.nodes)} node(s))")
        if config.replication.enabled:
            print(f"Standby site : {config.standby_site.name} ({len(config.standby_site.nodes)} node(s))")
            print(f"Replication  : {config.replication.mode}")
        else:
            print("Replication  : disabled")
        return 0

    if args.command == "precheck":
        state = NoopStateStore() if args.dry_run else StateStore(Path(args.state_dir), config.run_id)
        executor = SSHExecutor(config.ssh, dry_run=args.dry_run)
        runner = PrecheckRunner(config, executor, state=state, resume=not args.no_resume)
        results = runner.run()

        if args.json:
            print(json.dumps([item.to_dict() for item in results], indent=2))
        else:
            _print_precheck_results(results)

        return 1 if any(item.status == "FAIL" for item in results) else 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _print_precheck_results(results) -> None:
    current_host = None
    for item in results:
        if item.host != current_host:
            current_host = item.host
            print(f"\n[{current_host}]")
        print(f"{item.status:5} {item.name:28} {item.message}")
