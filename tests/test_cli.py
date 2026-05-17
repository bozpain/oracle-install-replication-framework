import os
import unittest
import tempfile
import threading
import uuid
from dataclasses import replace
from pathlib import Path

from oracle_auto.automation import AutomationRunner, AutomationStep, shell_script
from oracle_auto.cli import DEPLOYMENT_PHASE_ORDER, WORKFLOW_PHASE_ORDER, _with_remote_resume_override, main
from oracle_auto.config import NodeConfig, load_config
from oracle_auto.executor import CommandResult
from oracle_auto.precheck import _secret_env_check
from oracle_auto.progress import render_progress_line
from oracle_auto.report import render_html_report
from oracle_auto.secrets import redact
from oracle_auto.phase_builders.inventory import inventory_steps
from oracle_auto.phase_builders.installer import verify_installer_steps
from oracle_auto.phase_builders.os import prepare_os_steps
from oracle_auto.phase_builders.storage import configure_asm_storage_steps, prepare_storage_rules_steps
from oracle_auto.phase_builders.grid import install_grid_steps
from oracle_auto.phase_builders.database import create_database_steps, install_db_software_steps
from oracle_auto.phase_builders.dataguard import configure_dataguard_steps
from oracle_auto.phase_builders.validation import validate_deployment_steps
from oracle_auto.phase_builders.patching import apply_ojvm_patch_steps, patch_inventory_steps
from oracle_auto.response_files.grid import grid_response
from oracle_auto.state import NoopStateStore


class CliTest(unittest.TestCase):
    def _test_dir(self, name: str) -> Path:
        return Path(tempfile.mkdtemp(prefix=f"oracle-auto-{name}-{uuid.uuid4().hex}-"))

    def test_generate_plan_writes_html_and_json(self):
        tmp = self._test_dir("plan")
        code = main([
            "--report-dir",
            str(tmp),
            "generate-plan",
            "--config",
            "configs/sample-rac-dg.json",
            "--dataguard-mode",
            "broker",
        ])

        self.assertEqual(code, 0)
        self.assertTrue((tmp / "rac-adg-demo-plan.html").exists())
        self.assertTrue((tmp / "rac-adg-demo-plan.json").exists())
        self.assertTrue((tmp / "rac-adg-demo-runbook.sh").exists())
        self.assertTrue((tmp / "rac-adg-demo-phase-runbooks" / "prepare-os.sh").exists())
        self.assertTrue((tmp / "rac-adg-demo-phase-runbooks" / "apply-ojvm-patch.sh").exists())
        self.assertFalse((tmp / "rac-adg-demo-phase-runbooks" / "datapatch.sh").exists())

    def test_generate_report_prints_start_logger(self):
        tmp = self._test_dir("report")
        publish = self._test_dir("published-report")

        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        previous_path = os.environ.get("ORACLE_AUTO_REPORT_PUBLISH_PATH")
        previous_url = os.environ.get("ORACLE_AUTO_REPORT_URL_BASE")
        os.environ["ORACLE_AUTO_REPORT_PUBLISH_PATH"] = str(publish)
        os.environ["ORACLE_AUTO_REPORT_URL_BASE"] = "https://dbaportal/reports"
        try:
            with redirect_stdout(buffer):
                code = main([
                    "--report-dir",
                    str(tmp),
                    "--state-dir",
                    str(tmp),
                    "generate-report",
                    "--config",
                    "configs/sample-single.json",
                    "--dataguard-mode",
                    "broker",
                ])
        finally:
            if previous_path is None:
                os.environ.pop("ORACLE_AUTO_REPORT_PUBLISH_PATH", None)
            else:
                os.environ["ORACLE_AUTO_REPORT_PUBLISH_PATH"] = previous_path
            if previous_url is None:
                os.environ.pop("ORACLE_AUTO_REPORT_URL_BASE", None)
            else:
                os.environ["ORACLE_AUTO_REPORT_URL_BASE"] = previous_url

        self.assertEqual(code, 0)
        self.assertIn("RUN   generate-report:local:generate_report", buffer.getvalue())
        self.assertIn("Report published:", buffer.getvalue())
        self.assertIn("REPORT_URL=https://dbaportal/reports/single-gi-demo.html", buffer.getvalue())
        self.assertTrue((publish / "single-gi-demo.html").exists())

    def test_generate_report_can_publish_without_url(self):
        tmp = self._test_dir("report-no-url")
        publish = self._test_dir("published-report-no-url")

        import io
        from contextlib import redirect_stdout

        previous_path = os.environ.get("ORACLE_AUTO_REPORT_PUBLISH_PATH")
        previous_url = os.environ.get("ORACLE_AUTO_REPORT_URL_BASE")
        os.environ["ORACLE_AUTO_REPORT_PUBLISH_PATH"] = str(publish)
        os.environ.pop("ORACLE_AUTO_REPORT_URL_BASE", None)
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                code = main([
                    "--report-dir",
                    str(tmp),
                    "--state-dir",
                    str(tmp),
                    "generate-report",
                    "--config",
                    "configs/sample-single.json",
                ])
        finally:
            if previous_path is None:
                os.environ.pop("ORACLE_AUTO_REPORT_PUBLISH_PATH", None)
            else:
                os.environ["ORACLE_AUTO_REPORT_PUBLISH_PATH"] = previous_path
            if previous_url is None:
                os.environ.pop("ORACLE_AUTO_REPORT_URL_BASE", None)
            else:
                os.environ["ORACLE_AUTO_REPORT_URL_BASE"] = previous_url

        self.assertEqual(code, 0)
        self.assertIn("Report published:", buffer.getvalue())
        self.assertNotIn("Report URL:", buffer.getvalue())
        self.assertTrue((publish / "single-gi-demo.html").exists())

    def test_progress_line_reports_current_running_step(self):
        tmp = self._test_dir("progress")
        state = tmp / "run.json"
        state.write_text(
            '{"steps":{"verify-installer:db01:verify_installer":{"status":"running"},'
            '"prepare-os:db01:prepare_os":{"status":"done"}}}',
            encoding="utf-8",
        )

        line = render_progress_line(state)

        self.assertIn("done=1", line)
        self.assertIn("running=1", line)
        self.assertIn("verify-installer:db01:verify_installer", line)

    def test_storage_guardrail_blocks_real_execution(self):
        code = main([
            "configure-asm-storage",
            "--config",
            "configs/sample-rac-dg.json",
        ])

        self.assertEqual(code, 2)

    def test_validate_config_allows_standby_without_dataguard_mode(self):
        code = main([
            "validate-config",
            "--config",
            "configs/gcp-single-gi-lab.json",
        ])

        self.assertEqual(code, 0)

    def test_dataguard_action_requires_dataguard_mode_cli_arg(self):
        code = main([
            "configure-dataguard",
            "--config",
            "configs/gcp-single-gi-lab.json",
            "--dry-run",
        ])

        self.assertEqual(code, 2)

    def test_report_renders_standby_config_without_dataguard_mode(self):
        config = load_config(Path("configs/gcp-single-gi-lab.json"))

        html = render_html_report(config, [])

        self.assertIn("not selected", html)
        self.assertIn("Active Data Guard", html)

    def test_manual_dataguard_steps_prepare_network_auxiliary_and_duplicate(self):
        base = load_config(Path("configs/gcp-single-gi-lab.json"))
        config = replace(base, dataguard=replace(base.dataguard, configuration_method="manual"))

        steps = configure_dataguard_steps(config)
        names = [step.name for step in steps]
        command = "\n".join(step.command for step in steps)

        self.assertEqual(names, [
            "configure_dataguard_network",
            "configure_dataguard_network",
            "validate_dataguard_network",
            "validate_dataguard_network",
            "ensure_primary_archivelog",
            "configure_primary_dataguard",
            "export_primary_dataguard_baseline",
            "prepare_standby_dataguard_baseline_directory",
            "transfer_primary_pfile_to_standby",
            "transfer_primary_passwordfile_to_standby",
            "refresh_dataguard_duplicate_network",
            "refresh_dataguard_duplicate_network",
            "validate_dataguard_duplicate_network",
            "validate_dataguard_duplicate_network",
            "prepare_standby_auxiliary",
            "duplicate_standby_database",
            "configure_dataguard_final_network",
            "configure_dataguard_final_network",
            "validate_dataguard_final_network",
            "validate_dataguard_final_network",
            "start_managed_recovery",
            "verify_primary_dataguard",
            "verify_standby_dataguard",
        ])
        self.assertNotIn("configure_broker", names)
        self.assertIn("tnsnames.ora", command)
        self.assertIn("SID_LIST_LISTENER", command)
        self.assertIn("(HOST = 10.128.0.3)", command)
        self.assertIn("(HOST = 10.128.0.4)", command)
        self.assertIn("tnsping ORCL", command)
        self.assertIn("tnsping ORCLSTBY", command)
        self.assertIn("ALTER DATABASE ARCHIVELOG", command)
        self.assertIn("grep -Eqi", command)
        self.assertIn("^[[:space:]]*ARCHIVELOG[[:space:]]*$", command)
        self.assertIn("Primary database ORCL already runs in ARCHIVELOG mode.", command)
        self.assertIn("ALTER DATABASE ADD STANDBY LOGFILE THREAD", command)
        self.assertIn("STARTUP NOMOUNT", command)
        self.assertIn("CREATE SPFILE=", command)
        self.assertIn("+DATA/ORCLSTBY/PARAMETERFILE/spfileORCLSTBY.ora", command)
        self.assertIn("initORCLSTBY.ora", command)
        self.assertIn("WHENEVER SQLERROR CONTINUE", command)
        self.assertIn("SHUTDOWN IMMEDIATE", command)
        self.assertIn("asmcmd ls \"$spfile_alias\"", command)
        self.assertIn("Standby ASM spfile already exists; preserving it for resume.", command)
        self.assertIn("+DATA/ORCLSTBY/PARAMETERFILE/spfileORCLSTBY.ora", command)
        self.assertIn("srvctl add database -db ORCLSTBY", command)
        self.assertIn("DUPLICATE TARGET DATABASE FOR STANDBY FROM ACTIVE DATABASE", command)
        self.assertIn("Refreshing Data Guard tnsnames before RMAN duplicate.", command)
        self.assertIn("CONNECT TARGET", command)
        self.assertIn("CONNECT AUXILIARY", command)
        self.assertIn("ALTER SYSTEM ARCHIVE LOG CURRENT", command)
        self.assertIn("CREATE PFILE=", command)
        self.assertIn("/tmp/oracle-auto-dataguard-baseline/initORCL.ora", command)
        self.assertIn("chown oracle:oinstall /tmp/oracle-auto-dataguard-baseline", command)
        self.assertIn("rm -f /tmp/oracle-auto-dataguard-baseline/initORCL.ora /tmp/oracle-auto-dataguard-baseline/orapwORCL", command)
        self.assertIn("chown rori_learning /tmp/oracle-auto-dataguard-baseline /tmp/oracle-auto-dataguard-baseline/initORCL.ora /tmp/oracle-auto-dataguard-baseline/orapwORCL", command)
        self.assertIn("Prepare standby baseline transfer directory", command)
        self.assertIn("chown rori_learning /tmp/oracle-auto-dataguard-baseline", command)
        pfile_transfer = next(step for step in steps if step.name == "transfer_primary_pfile_to_standby")
        pwfile_transfer = next(step for step in steps if step.name == "transfer_primary_passwordfile_to_standby")
        self.assertIn("scp", pfile_transfer.command)
        self.assertIn("scp", pwfile_transfer.command)
        self.assertIn("orapwORCL", command)
        self.assertIn("cp /tmp/oracle-auto-dataguard-baseline/orapwORCL /u01/app/oracle/product/19.0.0/dbhome_1/dbs/orapwORCLSTBY", command)
        self.assertIn("LOG_ARCHIVE_CONFIG", command)
        self.assertIn("DG_CONFIG=(ORCL,ORCLSTBY)", command)
        self.assertIn("DB_UNIQUE_NAME=ORCLSTBY", command)
        self.assertIn("fal_server", command)
        self.assertIn("fal_client", command)
        self.assertIn("SELECT dest_id, status, type, database_mode, recovery_mode, destination, error", command)
        self.assertIn("WHERE dest_id <= 2 OR destination IS NOT NULL", command)
        self.assertNotIn("target, destination", command)
        self.assertNotIn("WHERE target = 'STANDBY'", command)
        self.assertIn("dataguard_stats", command)
        self.assertIn("# BEGIN ORACLE-AUTO DATAGUARD TNSNAMES", command)
        self.assertIn("# END ORACLE-AUTO DATAGUARD TNSNAMES", command)
        self.assertIn("tns_file=/u01/app/oracle/product/19.0.0/dbhome_1/network/admin/tnsnames.ora", command)
        self.assertIn("awk -v aliases=", command)
        self.assertIn("ORCL ORCLSTBY", command)
        self.assertIn("remove_alias[managed[i]] = 1", command)
        self.assertIn("alias_name in remove_alias", command)
        self.assertIn("!skip{print}", command)
        self.assertNotIn("cat > /u01/app/oracle/product/19.0.0/dbhome_1/network/admin/tnsnames.ora", command)
        self.assertNotIn("cp /u01/app/oracle/product/19.0.0/dbhome_1/network/admin/tnsnames.ora /u01/app/19.0.0/grid/network/admin/tnsnames.ora", command)
        self.assertNotIn("tns_file=/u01/app/19.0.0/grid/network/admin/tnsnames.ora", command)

        primary_refresh = next(
            step for step in steps if step.name == "refresh_dataguard_duplicate_network" and step.node.host == "ora-primary-01"
        )
        standby_refresh = next(
            step for step in steps if step.name == "refresh_dataguard_duplicate_network" and step.node.host == "ora-standby-01"
        )
        primary_final = next(
            step for step in steps if step.name == "configure_dataguard_final_network" and step.node.host == "ora-primary-01"
        )
        standby_final = next(
            step for step in steps if step.name == "configure_dataguard_final_network" and step.node.host == "ora-standby-01"
        )
        self.assertNotIn("listener_file", primary_refresh.command)
        self.assertNotIn("lsnrctl", primary_refresh.command)
        self.assertNotIn("listener_file", primary_final.command)
        self.assertNotIn("lsnrctl", primary_final.command)
        self.assertIn("SID_LIST_LISTENER", standby_refresh.command)
        self.assertIn("SID_NAME = ORCLSTBY", standby_refresh.command)
        self.assertIn("LISTENER =", standby_refresh.command)
        self.assertIn("(HOST = ora-standby-01)(PORT = 1521)", standby_refresh.command)
        self.assertIn("lsnrctl reload LISTENER", standby_refresh.command)
        self.assertIn("listener_file", standby_final.command)
        self.assertIn("LISTENER =", standby_final.command)
        self.assertIn("(HOST = ora-standby-01)(PORT = 1521)", standby_final.command)
        self.assertIn("lsnrctl reload LISTENER || true", standby_final.command)
        self.assertNotIn("SID_LIST_LISTENER", standby_final.command)

        for step in steps:
            if step.name in {
                "refresh_dataguard_duplicate_network",
                "validate_dataguard_duplicate_network",
                "export_primary_dataguard_baseline",
                "prepare_standby_dataguard_baseline_directory",
                "transfer_primary_pfile_to_standby",
                "transfer_primary_passwordfile_to_standby",
                "configure_dataguard_final_network",
            }:
                self.assertTrue(step.force_rerun)
            else:
                self.assertFalse(step.force_rerun)
            if step.transfer:
                self.assertNotIn("oracle-auto remote marker wrapper", step.command)
            else:
                self.assertIn("oracle-auto remote marker wrapper", step.command)
                self.assertIn(f"/u01/stage/oracle-auto/state/{step.phase.replace('-', '_')}", step.command)

    def test_broker_dataguard_adds_broker_after_manual_steps(self):
        base = load_config(Path("configs/gcp-single-gi-lab.json"))
        config = replace(base, dataguard=replace(base.dataguard, configuration_method="broker"))

        steps = configure_dataguard_steps(config)

        self.assertEqual(steps[-1].name, "configure_broker")
        self.assertIn("CREATE CONFIGURATION", steps[-1].command)
        self.assertIn("DG_BROKER_START=TRUE", steps[-1].command)
        self.assertIn("StaticConnectIdentifier", steps[-1].command)
        self.assertIn("VALIDATE DATABASE", steps[-1].command)
        self.assertIn("ORCL", steps[-1].command)
        self.assertIn("ORCLSTBY", steps[-1].command)
        self.assertIn("SHOW CONFIGURATION VERBOSE", steps[-1].command)

    def test_validate_deployment_uses_safe_sqlplus_heredoc(self):
        config = load_config(Path("configs/gcp-single-gi-lab.json"))

        steps = validate_deployment_steps(config)
        command = next(step.command for step in steps if step.name == "validate_primary_database")
        standby_command = next(step.command for step in steps if step.name == "validate_standby_database")

        self.assertIn("export ORACLE_SID=ORCL", command)
        self.assertIn("/u01/app/oracle/product/19.0.0/dbhome_1/bin/sqlplus -s / as sysdba", command)
        self.assertIn("SELECT name, db_unique_name, open_mode, database_role, switchover_status FROM v$database;", command)
        self.assertIn("PRIMARY OPERATIONAL READINESS", command)
        self.assertIn("PASS: primary database is open read/write for application workload", command)
        self.assertIn("v$archive_dest_status", command)
        self.assertIn("PRIMARY DATA GUARD TRANSPORT READINESS", command)
        self.assertIn("PRIMARY SWITCHOVER READINESS", command)
        self.assertNotIn("sudo -iu oracle bash -lc \"export ORACLE_SID=ORCL", command)
        self.assertNotIn("SQLSELECT", command)
        self.assertIn("lsnrctl status LISTENER", command)
        self.assertIn("status READY", command)
        self.assertIn("PASS: listener service", command)
        self.assertIn("export ORACLE_SID=ORCLSTBY", standby_command)
        self.assertIn("STANDBY OPERATIONAL READINESS", standby_command)
        self.assertIn("SELECT name, value, unit FROM v$dataguard_stats;", standby_command)
        self.assertIn("SELECT * FROM v$archive_gap;", standby_command)
        self.assertIn("STANDBY ARCHIVE GAP READINESS", standby_command)
        self.assertIn("PASS: no archive gap reported by standby", standby_command)
        self.assertIn("last_received_sequence", standby_command)
        self.assertIn("last_applied_sequence", standby_command)
        self.assertIn("v$managed_standby", standby_command)
        self.assertIn("STANDBY APPLY READINESS", standby_command)
        self.assertIn("STANDBY SWITCHOVER READINESS", standby_command)
        for step in steps:
            self.assertTrue(step.force_rerun)
            self.assertNotIn("oracle-auto remote marker wrapper", step.command)

    def test_dataguard_uses_configured_standby_redo_log_size(self):
        base = load_config(Path("configs/gcp-single-gi-lab.json"))
        config = replace(
            base,
            dataguard=replace(base.dataguard, configuration_method="manual", standby_redo_log_size="512M"),
        )

        command = next(step.command for step in configure_dataguard_steps(config) if step.name == "configure_primary_dataguard")

        self.assertIn("SIZE 512M", command)
        self.assertNotIn("SIZE 2G", command)

    def test_rac_dataguard_registers_instances_and_switches_final_tns_to_scan(self):
        base = load_config(Path("configs/sample-rac-dg.json"))
        config = replace(base, dataguard=replace(base.dataguard, configuration_method="broker"))

        steps = configure_dataguard_steps(config)
        names = [step.name for step in steps]
        command = "\n".join(step.command for step in steps)
        broker_command = steps[-1].command

        self.assertEqual(names.count("configure_dataguard_network"), 4)
        self.assertEqual(names.count("configure_dataguard_final_network"), 4)
        self.assertEqual(names.count("validate_dataguard_final_network"), 4)
        self.assertEqual(names.count("ensure_primary_archivelog"), 1)
        self.assertIn("export ORACLE_SID=ORCL_A1", command)
        self.assertIn("export ORACLE_SID=ORCL_B1", command)
        self.assertIn("srvctl start instance -db ORCL_A -instance ORCL_A1 -startoption MOUNT", command)
        self.assertIn("SID_NAME = ORCL_B1", command)
        self.assertIn("SID_NAME = ORCL_B2", command)
        self.assertIn("srvctl config database -db ORCL_B | grep -qw ORCL_B1", command)
        self.assertIn("srvctl add instance -db ORCL_B -instance ORCL_B1 -node db1-site-b.example.com", command)
        self.assertIn("srvctl add instance -db ORCL_B -instance ORCL_B2 -node db2-site-b.example.com", command)
        self.assertIn("awk -v primary_unique=ORCL_A -v standby_unique=ORCL_B", command)
        self.assertIn("body_l=tolower(body)", command)
        self.assertIn("remote_listener", command)
        self.assertIn("scan-site-b.example.com:1521", command)
        self.assertIn("ORCL_B1.local_listener", command)
        self.assertIn("HOST=db1-site-b.example.com", command)
        self.assertIn("ORCL_B2.local_listener", command)
        self.assertIn("HOST=db2-site-b.example.com", command)
        self.assertIn("(HOST = 192.168.113.101)", command)
        self.assertIn("(HOST = scan-site-a.example.com)", command)
        self.assertIn("(HOST = scan-site-b.example.com)", command)
        self.assertIn("HOST=scan-site-a.example.com", broker_command)
        self.assertIn("HOST=scan-site-b.example.com", broker_command)

    def test_full_guardrail_blocks_real_execution_without_flags(self):
        code = main([
            "full",
            "--config",
            "configs/sample-single.json",
            "--dataguard-mode",
            "broker",
        ])

        self.assertEqual(code, 2)

    def test_full_dry_run_writes_report(self):
        tmp = self._test_dir("full")
        code = main([
            "--report-dir",
            str(tmp),
            "--state-dir",
            str(tmp),
            "full",
            "--config",
            "configs/sample-single.json",
            "--dataguard-mode",
            "broker",
            "--dry-run",
        ])

        self.assertEqual(code, 0)
        self.assertTrue((tmp / "single-gi-demo.html").exists())

    def test_full_workflow_updates_opatch_inside_install_phases(self):
        self.assertNotIn("update-opatch", WORKFLOW_PHASE_ORDER)
        self.assertNotIn("update-opatch", DEPLOYMENT_PHASE_ORDER)
        self.assertLess(WORKFLOW_PHASE_ORDER.index("install-db-software"), WORKFLOW_PHASE_ORDER.index("apply-ojvm-patch"))

    def test_dry_run_results_are_labeled_dryrun(self):
        step = AutomationStep(
            phase="prepare-os",
            name="prepare_os",
            node=NodeConfig(host="db01", public_ip="192.0.2.10"),
            command="true",
            title="Prepare OS",
        )
        result = AutomationRunner._to_step_result(
            step,
            CommandResult(
                host="db01",
                command="ssh oracle@db01 'true'",
                returncode=0,
                stdout="DRY-RUN",
                stderr="",
                skipped=True,
            ),
        )

        self.assertEqual(result.status, "DRYRUN")

    def test_runner_prints_step_start_before_execution(self):
        step = AutomationStep(
            phase="verify-installer",
            name="verify_installer",
            node=NodeConfig(host="db01", public_ip="192.0.2.10"),
            command="true",
            title="Verify installer files",
        )

        class RecordingExecutor:
            def run(self, node, command, timeout=60):
                return CommandResult(node.host, command, 0, "ok", "")

        class MemoryState:
            def is_done(self, key):
                return False

            def mark_running(self, key):
                pass

            def mark_done(self, key, details):
                pass

            def mark_failed(self, key, details):
                pass

        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            AutomationRunner(RecordingExecutor(), MemoryState()).run([step])

        self.assertIn("RUN   verify-installer:db01:verify_installer", buffer.getvalue())

    def test_force_rerun_step_ignores_completed_local_state(self):
        step = AutomationStep(
            phase="install-db-software",
            name="install_db_home_site-a",
            node=NodeConfig(host="db01", public_ip="192.0.2.10"),
            command="true",
            title="Install DB home",
            force_rerun=True,
        )

        class RecordingExecutor:
            def __init__(self):
                self.calls = 0

            def run(self, node, command, timeout=60):
                self.calls += 1
                return CommandResult(node.host, command, 0, "ok", "")

        class DoneState:
            def is_done(self, key):
                return True

            def mark_running(self, key):
                pass

            def mark_done(self, key, details):
                pass

            def mark_failed(self, key, details):
                pass

        executor = RecordingExecutor()
        result = AutomationRunner(executor, DoneState()).run([step])[0]

        self.assertEqual(executor.calls, 1)
        self.assertEqual(result.status, "PASS")

    def test_runner_parallelizes_host_chains_and_preserves_local_order(self):
        barrier = threading.Barrier(2)
        calls: list[tuple[str, str]] = []
        calls_lock = threading.Lock()

        class ParallelExecutor:
            def run(self, node, command, timeout=60):
                with calls_lock:
                    calls.append((node.host, command))
                if command == "first":
                    barrier.wait(timeout=2)
                return CommandResult(node.host, command, 0, "ok", "")

        steps = [
            AutomationStep("install-grid", "first_a", NodeConfig(host="site-a", public_ip="192.0.2.1"), "first", "A1"),
            AutomationStep("install-grid", "second_a", NodeConfig(host="site-a", public_ip="192.0.2.1"), "second", "A2"),
            AutomationStep("install-grid", "first_b", NodeConfig(host="site-b", public_ip="192.0.2.2"), "first", "B1"),
            AutomationStep("install-grid", "second_b", NodeConfig(host="site-b", public_ip="192.0.2.2"), "second", "B2"),
        ]

        results = AutomationRunner(
            ParallelExecutor(),
            NoopStateStore(),
            parallel_by_host=True,
        ).run(steps)

        self.assertEqual([result.name for result in results], ["first_a", "second_a", "first_b", "second_b"])
        self.assertLess(calls.index(("site-a", "first")), calls.index(("site-a", "second")))
        self.assertLess(calls.index(("site-b", "first")), calls.index(("site-b", "second")))

    def test_precheck_parallelizes_host_chains(self):
        from oracle_auto.precheck import PrecheckRunner

        barrier = threading.Barrier(2)
        calls: list[tuple[str, str]] = []
        lock = threading.Lock()

        class FakeExecutor:
            def run(self, node, command, timeout=60):
                if command == "printf ok":
                    with lock:
                        calls.append((node.host, command))
                    barrier.wait(timeout=2)
                return CommandResult(node.host, command, 0, "ok", "")

        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        runner = PrecheckRunner(config, FakeExecutor(), state=NoopStateStore())
        results = runner.run()

        self.assertEqual(len(calls), 2)
        self.assertEqual({host for host, _command in calls}, {"ora-primary-01", "ora-standby-01"})
        self.assertTrue(all(item.status == "PASS" for item in results))

    def test_verify_installer_has_no_framework_timeout(self):
        config = load_config(Path("configs/sample-single.json"))

        self.assertIsNone(verify_installer_steps(config)[0].timeout)

    def test_verify_installer_prints_zip_progress(self):
        config = load_config(Path("configs/sample-single.json"))
        command = verify_installer_steps(config)[0].command

        self.assertIn("verify_zip_integrity LINUX.X64_193000_grid_home.zip", command)
        self.assertIn("verify_zip_integrity p_ojvm_19.30_linux_x86-64.zip", command)
        self.assertIn("Integrity check: $file", command)
        self.assertIn("Content check: gridSetup.sh", command)
        self.assertNotIn("grep -q 'gridSetup.sh'", command)

    def test_verify_installer_caches_zip_integrity_per_file(self):
        config = load_config(Path("configs/sample-single.json"))
        command = verify_installer_steps(config)[0].command

        self.assertIn("VERIFY_CACHE_DIR=/u01/stage/installer-checks/zip-integrity", command)
        self.assertIn('Integrity check: $file (cached)', command)
        self.assertIn("stat -c", command)
        self.assertIn("%s:%Y", command)
        self.assertIn("verify_zip_integrity LINUX.X64_193000_grid_home.zip", command)

    def test_precheck_keeps_installer_checks_lightweight(self):
        from oracle_auto.precheck import PrecheckRunner

        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        runner = PrecheckRunner(config, executor=None, state=NoopStateStore())
        checks = {check.name: check for check in runner._checks_for(config.primary_site.nodes[0])}

        self.assertIn("installer_zip_files", checks)
        self.assertIn("installer_zip_contents", checks)
        self.assertNotIn("installer_zip_integrity", checks)
        self.assertNotIn("unzip -t", checks["installer_zip_files"].command)
        self.assertNotIn("unzip -t", checks["installer_zip_contents"].command)

    def test_storage_prepares_multipath_udev_rules_without_oracleasm_symlinks(self):
        config = load_config(Path("configs/sample-rac-dg.json"))
        command = prepare_storage_rules_steps(config)[0].command

        self.assertIn("DM_UUID=mpath-360060e8008a3cf000050a3cf00000101", command)
        self.assertIn("/dev/disk/by-id/dm-uuid-mpath-360060e8008a3cf000050a3cf00000101", command)
        self.assertIn("multipath -ll", command)
        self.assertIn("/etc/udev/rules.d/99-oracle-asm.rules", command)
        self.assertIn(
            'KERNEL=="dm-*", ENV{DM_UUID}=="mpath-360060e8008a3cf000050a3cf00000101", SYMLINK+="asm/OCR01", OWNER:="grid", GROUP:="asmdba", MODE="0660"',
            command,
        )
        self.assertIn("udevadm control --reload-rules", command)
        self.assertIn("udevadm trigger", command)
        self.assertIn("resolve_asm_source_device OCR01", command)
        self.assertIn("oracleasm createdisk OCR01", command)
        self.assertNotIn('SYMLINK+="oracleasm/', command)
        self.assertNotIn("/dev/oracleasm/", command)

    def test_precheck_storage_inspection_uses_sudo(self):
        from oracle_auto.precheck import _disk_signature_check, _disk_size_check

        config = load_config(Path("configs/gcp-single-gi-lab.json"))

        self.assertIn("sudo -n wipefs", _disk_signature_check(config))
        self.assertIn("sudo -n blockdev", _disk_size_check(config))

    def test_precheck_warns_when_asmlib_packages_are_not_visible(self):
        from oracle_auto.precheck import PrecheckRunner

        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        runner = PrecheckRunner(config, executor=None, state=NoopStateStore())
        checks = {check.name: check for check in runner._checks_for(config.primary_site.nodes[0])}

        self.assertIn("asmlib_packages", checks)
        self.assertIn("dnf list oracleasm-support", checks["asmlib_packages"].command)
        self.assertIn("local RPM in configured sources_path", checks["asmlib_packages"].command)
        self.assertTrue(checks["asmlib_packages"].warn_only)

    def test_precheck_warnings_are_retried_on_resume(self):
        from oracle_auto.precheck import PrecheckRunner

        class FakeExecutor:
            def __init__(self):
                self.asmlib_calls = 0

            def run(self, node, command, timeout=60):
                if "oracleasm-support" in command:
                    self.asmlib_calls += 1
                    return CommandResult(node.host, command, 1, "", "No matching Packages to list")
                return CommandResult(node.host, command, 0, "ok", "")

        class MemoryState:
            def __init__(self):
                self.statuses = {}

            def is_done(self, key):
                return self.statuses.get(key) == "done"

            def mark_running(self, key):
                self.statuses[key] = "running"

            def mark_done(self, key, details):
                self.statuses[key] = "done"

            def mark_failed(self, key, details):
                self.statuses[key] = "failed"

            def mark_warning(self, key, details):
                self.statuses[key] = "warning"

        config = load_config(Path("configs/sample-single.json"))
        executor = FakeExecutor()
        state = MemoryState()

        PrecheckRunner(config, executor, state=state).run()
        PrecheckRunner(config, executor, state=state).run()

        warning_steps = [
            key for key, status in state.statuses.items()
            if key.endswith(":asmlib_packages") and status == "warning"
        ]
        self.assertEqual(len(warning_steps), 2)
        self.assertEqual(executor.asmlib_calls, 4)

    def test_precheck_dnf_checks_have_no_framework_timeout(self):
        from oracle_auto.precheck import PrecheckRunner

        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        runner = PrecheckRunner(config, executor=None, state=NoopStateStore())
        checks = {check.name: check for check in runner._checks_for(config.primary_site.nodes[0])}

        self.assertIsNone(checks["oracle_yum_repo"].timeout)
        self.assertIsNone(checks["preinstall_package"].timeout)
        self.assertIsNone(checks["asmlib_packages"].timeout)

    def test_precheck_detects_vm_storage_mode_without_warning(self):
        from oracle_auto.precheck import PrecheckRunner

        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        runner = PrecheckRunner(config, executor=None, state=NoopStateStore())
        checks = {check.name: check for check in runner._checks_for(config.primary_site.nodes[0])}

        self.assertIn("storage_mode_detection", checks)
        self.assertFalse(checks["storage_mode_detection"].warn_only)
        self.assertIn("virtual-machine/direct-asmlib", checks["storage_mode_detection"].command)
        self.assertNotIn("multipath_health", checks)

    def test_precheck_requires_multipath_when_dm_uuid_is_configured(self):
        from oracle_auto.precheck import PrecheckRunner

        config = load_config(Path("configs/sample-rac-dg.json"))
        runner = PrecheckRunner(config, executor=None, state=NoopStateStore())
        checks = {check.name: check for check in runner._checks_for(config.primary_site.nodes[0])}

        self.assertIn("storage_mode_detection", checks)
        self.assertIn("DM_UUID/multipath", checks["storage_mode_detection"].command)
        self.assertIn("exit 1", checks["storage_mode_detection"].command)

    def test_precheck_checks_asmlib_kernel_interface(self):
        from oracle_auto.precheck import PrecheckRunner

        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        runner = PrecheckRunner(config, executor=None, state=NoopStateStore())
        checks = {check.name: check for check in runner._checks_for(config.primary_site.nodes[0])}

        self.assertIn("asmlib_kernel_interface", checks)
        self.assertIn("ASMLIB v3 requires UEK R7+ (5.15+)", checks["asmlib_kernel_interface"].command)
        self.assertIn("oracleasm.ko", checks["asmlib_kernel_interface"].command)

    def test_install_steps_apply_targeted_ru_patches(self):
        config = load_config(Path("configs/sample-single.json"))
        grid_steps = install_grid_steps(config)
        grid_command = grid_steps[0].command
        root_command = next(step.command for step in grid_steps if step.name.startswith("root_scripts_site-a_"))
        config_tools_command = next(step.command for step in grid_steps if step.name == "config_tools_site-a")
        db_steps = install_db_software_steps(config)
        db_command = db_steps[0].command
        db_root_command = next(step.command for step in db_steps if step.name.startswith("db_root_script_site-a_"))

        self.assertTrue(grid_steps[0].force_rerun)
        self.assertNotIn("oracle-auto remote marker wrapper", grid_command)
        self.assertTrue(db_steps[0].force_rerun)
        self.assertNotIn("oracle-auto remote marker wrapper", db_command)
        self.assertIn("Cleaning unconfigured Grid home before install/resume.", grid_command)
        self.assertIn("Grid Infrastructure already configured; not cleaning Grid home.", grid_command)
        self.assertIn("Grid Infrastructure is already configured; preserving Grid home and skipping software setup.", grid_command)
        self.assertIn("Ensuring at least 512 MiB swap for Oracle installer", grid_command)
        self.assertIn("inventory_loc=/u01/app/oraInventory", grid_command)
        self.assertIn("chmod 664 /etc/oraInst.loc", grid_command)
        self.assertIn("cat > /u01/app/19.0.0/grid/oraInst.loc", grid_command)
        self.assertIn("chown grid:oinstall /u01/app/19.0.0/grid/oraInst.loc", grid_command)
        self.assertIn("rm -rf /u01/app/19.0.0/grid/OPatch", grid_command)
        self.assertIn("sudo -iu grid /u01/app/19.0.0/grid/OPatch/opatch version", grid_command)
        self.assertIn("sudo -iu grid env CV_ASSUME_DISTID=OL7", grid_command)
        self.assertIn("ORACLE_BASE=/u01/app/grid /u01/app/19.0.0/grid/gridSetup.sh", grid_command)
        self.assertIn("ASMSNMP_PASSWORD=", grid_command)
        self.assertIn("chmod 600 /u01/stage/responses/grid-site-a.rsp", grid_command)
        self.assertIn("GRID_SETUP_LOG=/u01/stage/logs/gridSetup-site-a.out", grid_command)
        self.assertIn("Successfully Setup Software|execute the following script|executeConfigTools", grid_command)
        self.assertIn("Grid setup reached root/config-tool phase; validating RU version before continuing.", grid_command)
        self.assertIn("Validating Grid RU with oraversion before root scripts/config tools.", grid_command)
        self.assertIn("Grid RU validation passed by oraversion.", grid_command)
        self.assertIn("oraversion -compositeVersion", grid_command)
        self.assertNotIn("opatch lspatches", grid_command)
        self.assertNotIn("opatch lsinventory", grid_command)
        self.assertNotIn("Grid software setup completed; root scripts and config tools will run in following steps.", grid_command)
        self.assertIn("Grid software and RU already installed; skipping software setup and continuing with root scripts/config tools.", grid_command)
        self.assertIn("p_gi_19.30_linux_x86-64.zip", grid_command)
        self.assertIn("chmod a+rx /u01/sources", grid_command)
        self.assertIn("rm -rf /u01/sources/38629535", grid_command)
        self.assertIn("chmod -R a+rX /u01/sources/38629535", grid_command)
        self.assertIn('sudo -iu grid ls -ld "$GRID_PATCH_TOP"', grid_command)
        self.assertNotIn("sudo -iu grid env ORACLE_HOME=/u01/app/19.0.0/grid ORACLE_BASE=/tmp", grid_command)
        self.assertIn('-applyRU "$GRID_PATCH_TOP"', grid_command)
        self.assertIn("/u01/app/oraInventory/orainstRoot.sh", root_command)
        self.assertIn("/u01/app/19.0.0/grid/root.sh", root_command)
        self.assertIn("/u01/app/19.0.0/grid/bin/crsctl check has", root_command)
        self.assertIn("sudo -iu grid /u01/app/19.0.0/grid/bin/asmcmd lsdg || true", root_command)
        self.assertIn("/u01/app/19.0.0/grid/bin/crsctl check has", config_tools_command)
        self.assertIn("/u01/app/19.0.0/grid/bin/asmcmd lsdg", config_tools_command)
        self.assertIn("ASMSNMP_PASSWORD=", config_tools_command)
        self.assertIn("cat > /u01/stage/responses/grid-site-a.rsp", config_tools_command)
        self.assertIn("oracle.install.asm.diskGroup.disks=ORCL:DATA01", config_tools_command)
        self.assertIn("oracle.install.asm.diskGroup.diskDiscoveryString=ORCL:*", config_tools_command)
        self.assertIn("Repairing unexpected root-owned Grid image files before executeConfigTools", config_tools_command)
        self.assertIn("find /u01/app/19.0.0/grid -xdev -user root ! -perm /6000 -exec chown grid:oinstall {} +", config_tools_command)
        self.assertIn("Seeding grid SSH known_hosts for Oracle CVU strict host checks", config_tools_command)
        self.assertIn("ssh-keyscan -T 10 -t rsa,ecdsa,ed25519", config_tools_command)
        self.assertIn("2>/dev/null >> /home/grid/.ssh/known_hosts || true", config_tools_command)
        self.assertIn("/etc/ssh/ssh_known_hosts", config_tools_command)
        self.assertIn("db1-site-a.example.com", config_tools_command)
        self.assertIn("db1-site-a", config_tools_command)
        self.assertIn("Grid configuration tools failed; extracting recent Oracle log errors", config_tools_command)
        self.assertIn("gridConfigTools-site-a.out", config_tools_command)
        self.assertIn("Running ASMCA directly with configured ASM disk string", config_tools_command)
        self.assertIn("/u01/app/19.0.0/grid/bin/asmca -silent -configureASM", config_tools_command)
        self.assertIn("-diskString", config_tools_command)
        self.assertIn("ORCL:*", config_tools_command)
        self.assertIn("-diskList ORCL:DATA01", config_tools_command)
        self.assertIn("ASMCA failed using configured ASM discovery. Not retrying with another storage mode.", config_tools_command)
        self.assertNotIn("/dev/oracleasm/", config_tools_command)
        self.assertIn("Grid ASM configuration complete; skipping OUI executeConfigTools replay.", config_tools_command)
        self.assertIn("Single-GI ASM DATA diskgroup already exists; treating ASM config tools as complete", config_tools_command)
        self.assertIn("-newer \"$config_tools_stamp\"", config_tools_command)
        self.assertIn("Captured executeConfigTools output", config_tools_command)
        self.assertIn("SEVERE|ERROR|FATAL|INS-", config_tools_command)
        self.assertIn("-executeConfigTools -responseFile /u01/stage/responses/grid-site-a.rsp -silent", config_tools_command)
        self.assertIn("Grid configuration tools appear complete; skipping executeConfigTools.", config_tools_command)
        self.assertIn("p_dbru_19.30_linux_x86-64.zip", db_command)
        self.assertIn("Ensuring at least 512 MiB swap for Oracle installer", db_command)
        self.assertIn("usermod -aG asmadmin,asmdba,asmoper,dba,racdba grid", db_command)
        self.assertIn("usermod -aG dba,oper,backupdba,dgdba,kmdba,racdba,asmdba oracle", db_command)
        self.assertIn("cat > /u01/app/oracle/product/19.0.0/dbhome_1/oraInst.loc", db_command)
        self.assertIn("chown oracle:oinstall /u01/app/oracle/product/19.0.0/dbhome_1/oraInst.loc", db_command)
        self.assertIn("DB_INSTALL_LOG=/u01/stage/logs/dbInstall-site-a.out", db_command)
        self.assertLess(db_command.index("DB_INSTALL_LOG=/u01/stage/logs/dbInstall-site-a.out"), db_command.index("DB_DATABASE_REGISTERED=false"))
        self.assertIn("DB_PREVIOUS_INSTALL_FAILED=false", db_command)
        self.assertIn("FATAL|ERROR|INS-|failed|failure", db_command)
        self.assertIn("Successfully Setup Software|execute the following script", db_command)
        self.assertIn("Existing Database Oracle version before home cleanup", db_command)
        self.assertIn("Database home is already usable for ORCL_A; not cleaning DB home.", db_command)
        self.assertIn("Cleaning Database home before install/resume.", db_command)
        self.assertIn("DB_HOME_INVENTORY_REGISTERED=false", db_command)
        self.assertIn("LOC=\"/u01/app/oracle/product/19.0.0/dbhome_1\"", db_command)
        self.assertIn("/u01/app/oracle/product/19.0.0/dbhome_1/oui/bin/runInstaller -silent -detachHome", db_command)
        self.assertIn("ORACLE_HOME_NAME=OraDB19Home1", db_command)
        self.assertIn("-invPtrLoc /u01/app/oracle/product/19.0.0/dbhome_1/oraInst.loc", db_command)
        self.assertIn("Database home is registered but not ready; detaching stale inventory entry before retry.", db_command)
        self.assertIn("Database home is still registered in central inventory after detachHome.", db_command)
        self.assertIn("find /u01/app/oracle/product/19.0.0/dbhome_1 -mindepth 1 -maxdepth 1 -exec rm -rf", db_command)
        self.assertIn("rm -rf /u01/app/oracle/product/19.0.0/dbhome_1/OPatch", db_command)
        self.assertIn("Updating Database OPatch before Database RU apply.", db_command)
        self.assertIn("sudo -iu oracle /u01/app/oracle/product/19.0.0/dbhome_1/OPatch/opatch version", db_command)
        self.assertIn("sudo -iu oracle env CV_ASSUME_DISTID=OL7", db_command)
        self.assertIn("oracle_auto_db_software_installed.marker", db_command)
        self.assertIn("Database setup reached root script phase; continuing with DB root script task.", db_command)
        self.assertIn("Successfully Setup Software|execute the following script", db_command)
        self.assertIn("ERROR: Database software setup failed. See $DB_INSTALL_LOG", db_command)
        self.assertIn("Validating Database RU with oraversion before root script.", db_command)
        self.assertIn("Database RU validation passed by oraversion.", db_command)
        self.assertIn("oraversion -compositeVersion", db_command)
        self.assertNotIn("opatch lspatches", db_command)
        self.assertNotIn("opatch lsinventory", db_command)
        self.assertIn("chmod -R a+rX /u01/sources/38632161", db_command)
        self.assertIn('-applyRU "$DB_PATCH_TOP"', db_command)
        self.assertIn("Validating oracle user ASM visibility after Database root script.", db_root_command)
        self.assertIn("ORACLE_HOME=/u01/app/19.0.0/grid", db_root_command)
        self.assertIn("/u01/app/19.0.0/grid/bin/sqlplus -L -s / as sysdba", db_root_command)
        self.assertIn("v$asm_diskgroup", db_root_command)

    def test_persistent_by_id_paths_are_labeled_with_asmlib(self):
        base = load_config(Path("configs/gcp-single-gi-lab.json"))
        config = replace(base, asm=replace(base.asm, storage_mode="asmlibv3"))
        command = prepare_storage_rules_steps(config)[0].command

        self.assertIn("ORACLE_AUTO_MULTIPATH=false", command)
        self.assertIn("multipath -ll", command)
        self.assertIn("No multipath devices detected; ASMLIB will label", command)
        self.assertIn("resolve_asm_source_device DATA1 /dev/disk/by-id/scsi-0Google_PersistentDisk_p-data-1", command)
        self.assertIn("resolve_asm_source_device RECO1 /dev/disk/by-id/scsi-0Google_PersistentDisk_p-reco-1", command)
        self.assertNotIn("/dev/disk/by-id/scsi-0Google_PersistentDisk_data-part2", command)
        self.assertIn("ORACLE_AUTO_ASMLIB_IOFILTER=n", command)
        self.assertIn("Direct ASMLIB mode detected; disabling ASMLIB I/O filter", command)
        self.assertIn('oracleasm configure -u grid -g asmdba -e -s y -m 2048 -f "$ORACLE_AUTO_ASMLIB_IOFILTER"', command)
        self.assertIn("chown grid:asmdba", command)
        self.assertNotIn("grid:asmadmin", command)
        self.assertIn("systemctl restart oracleasm || oracleasm init", command)
        self.assertIn("oracleasm status || true", command)
        self.assertLess(
            command.index("resolve_asm_source_device DATA1"),
            command.index("validate_asmlib_label DATA1"),
        )
        self.assertLess(command.index("oracleasm createdisk DATA1"), command.rindex("validate_asmlib_label DATA1"))
        self.assertIn("oracleasm querydisk -p", command)
        self.assertIn("LABEL=", command)
        self.assertIn("TYPE=", command)
        self.assertIn("oracleasm", command)
        self.assertIn("device_major=$((16#$device_major_hex))", command)
        self.assertIn("does not match configured device", command)
        self.assertIn("ASMLIB v3 kernel interface: UEK driverless/io_uring", command)
        self.assertIn("/boot/vmlinuz-5.15.0-320.202.8.2.el8uek.x86_64", command)
        self.assertIn("config-manager --set-enabled ol8_addons", command)
        self.assertIn("/u01/sources/oracleasmlib-3.1.1-1.el8.x86_64.rpm", command)
        self.assertNotIn("download.oracle.com/otn_software/asmlib", command)
        self.assertIn("oracleasm createdisk DATA1", command)
        self.assertIn("oracleasm listdisks", command)
        self.assertNotIn("/dev/oracleasm/", command)
        self.assertNotIn("ln -sfn", command)

    def test_raw_storage_uses_by_id_paths_without_asmlib_labels(self):
        base = load_config(Path("configs/gcp-single-gi-lab.json"))
        config = replace(base, asm=replace(base.asm, storage_mode="raw"))
        rules_command = prepare_storage_rules_steps(config)[0].command
        asm_command = configure_asm_storage_steps(config)[0].command
        response = grid_response(config, config.primary_site)

        self.assertIn("Writing raw ASM ownership rules", rules_command)
        self.assertIn("ENV{ID_SERIAL}", rules_command)
        self.assertIn("Raw ASM storage prepared", rules_command)
        self.assertNotIn("oracleasm createdisk", rules_command)
        self.assertNotIn("oracleasm configure", rules_command)
        self.assertIn("/dev/disk/by-id/scsi-0Google_PersistentDisk_p-data-1-part1", asm_command)
        self.assertIn("Using raw ASM storage", asm_command)
        self.assertNotIn("ORCL:DATA1", asm_command)
        self.assertIn("oracle.install.asm.diskGroup.disks=/dev/disk/by-id/scsi-0Google_PersistentDisk_p-data-1-part1", response)
        self.assertIn("oracle.install.asm.diskGroup.diskDiscoveryString=/dev/disk/by-id/scsi-0Google_PersistentDisk_p-data-1-part1", response)

    def test_afd_storage_uses_afd_labels(self):
        base = load_config(Path("configs/gcp-single-gi-lab.json"))
        config = replace(base, asm=replace(base.asm, storage_mode="afd"))
        rules_command = prepare_storage_rules_steps(config)[0].command
        asm_command = configure_asm_storage_steps(config)[0].command
        response = grid_response(config, config.primary_site)

        self.assertIn("AFD ASM storage prepared", rules_command)
        self.assertIn("asmcmd afd_label DATA1", asm_command)
        self.assertIn("asmcmd afd_scan", asm_command)
        self.assertIn("AFD:DATA1", asm_command)
        self.assertIn("oracle.install.asm.diskGroup.disks=AFD:DATA1", response)
        self.assertIn("oracle.install.asm.diskGroup.diskDiscoveryString=AFD:*", response)

    def test_standby_storage_uses_site_specific_paths(self):
        base = load_config(Path("configs/gcp-single-gi-lab.json"))
        config = replace(base, asm=replace(base.asm, storage_mode="asmlibv3"))
        command = prepare_storage_rules_steps(config)[1].command

        self.assertIn("resolve_asm_source_device DATA1 /dev/disk/by-id/scsi-0Google_PersistentDisk_s-data-1-part1", command)
        self.assertIn("oracleasm createdisk DATA1", command)
        self.assertNotIn("/dev/disk/by-id/scsi-0Google_PersistentDisk_p-data-1-part1", command)

    def test_non_multipath_storage_can_resolve_id_serial_and_id_wwn(self):
        import json
        import tempfile
        import uuid

        data = json.loads(Path("configs/gcp-single-gi-lab.json").read_text(encoding="utf-8"))
        data["asm"].pop("storage_mode", None)
        data["asm"]["data_disks"] = [{"id_serial": "scsi-3600ABCDEF001", "name": "DATA01"}]
        data["asm"]["reco_disks"] = [{"id_wwn": "0x600abcdef002", "name": "RECO01"}]
        path = Path(tempfile.mkdtemp(prefix=f"oracle-auto-byid-{uuid.uuid4().hex}-")) / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        config = load_config(path)
        command = prepare_storage_rules_steps(config)[0].command

        self.assertIn("ID_SERIAL=$id_serial", command)
        self.assertIn("ID_WWN=$id_wwn", command)
        self.assertIn("resolved=$(resolve_asm_source_device DATA01", command)
        self.assertIn("resolved=$(resolve_asm_source_device RECO01", command)
        self.assertNotIn("resolved=$(resolve_asm_source_device DATA01 /dev/disk/by-id", command)
        self.assertNotIn("resolved=$(resolve_asm_source_device RECO01 /dev/disk/by-id", command)
        self.assertIn("oracleasm createdisk DATA01", command)
        self.assertIn("oracleasm createdisk RECO01", command)

    def test_configured_grid_patch_id_selects_ru_bundle_top(self):
        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        command = install_grid_steps(config)[0].command

        self.assertIn("GRID_PATCH_TOP=/u01/sources/38629535", command)
        self.assertIn("oraversion -compositeVersion", command)
        self.assertNotIn("opatch lspatches", command)
        self.assertNotIn("^(38629535);", command)

    def test_configured_db_and_ojvm_patch_ids_select_patch_tops(self):
        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        db_command = install_db_software_steps(config)[0].command
        ojvm_command = apply_ojvm_patch_steps(config)[0].command

        self.assertIn("DB_PATCH_TOP=/u01/sources/38632161", db_command)
        self.assertIn("PATCH_TOP=/u01/sources/38523609", ojvm_command)

    def test_multipath_alias_path_is_labeled_with_asmlib(self):
        import json
        import tempfile
        import uuid

        data = json.loads(Path("configs/gcp-single-gi-lab.json").read_text(encoding="utf-8"))
        data["asm"].pop("storage_mode", None)
        data["asm"]["data_disks"][0]["site_paths"]["site-a"] = "/dev/mapper/ora_data01"
        data["asm"]["reco_disks"][0]["site_paths"]["site-a"] = "/dev/mapper/ora_reco01"
        path = Path(tempfile.mkdtemp(prefix=f"oracle-auto-mpath-{uuid.uuid4().hex}-")) / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        config = load_config(path)
        command = prepare_storage_rules_steps(config)[0].command

        self.assertIn("resolve_asm_source_device DATA1 /dev/mapper/ora_data01", command)
        self.assertIn("resolved=$(resolve_asm_source_device DATA1 /dev/mapper/ora_data01", command)
        self.assertIn("oracleasm createdisk DATA1", command)
        self.assertIn("oracleasm querydisk DATA1", command)

    def test_single_gi_grid_response_omits_cluster_only_fields(self):
        config = load_config(Path("configs/sample-single.json"))
        response = grid_response(config, config.primary_site)

        self.assertIn("oracle.install.option=HA_CONFIG", response)
        self.assertIn("oracle.install.asm.SYSASMPassword=$ASMSNMP_PASSWORD", response)
        self.assertIn("oracle.install.asm.monitorPassword=$ASMSNMP_PASSWORD", response)
        self.assertIn("oracle.install.asm.diskGroup.disks=ORCL:DATA01", response)
        self.assertIn("oracle.install.asm.diskGroup.diskDiscoveryString=ORCL:*", response)
        self.assertNotIn("oracle.install.crs.config.clusterNodeVIPs", response)
        self.assertNotIn("oracle.install.crs.config.clusterNodes", response)

    def test_rac_grid_response_embeds_vips_in_cluster_nodes(self):
        config = load_config(Path("configs/sample-rac-dg.json"))
        response = grid_response(config, config.primary_site)

        self.assertIn("oracle.install.option=CRS_CONFIG", response)
        self.assertIn("oracle.install.crs.config.clusterNodes=db1-site-a.example.com:db1-site-a-vip.example.com", response)
        self.assertNotIn("oracle.install.crs.config.clusterNodeVIPs", response)

    def test_ojvm_patch_runs_before_database_creation_phase(self):
        config = load_config(Path("configs/sample-single.json"))
        steps = apply_ojvm_patch_steps(config)

        self.assertEqual(len(steps), 2)
        self.assertIn("p_ojvm_19.30_linux_x86-64.zip", steps[0].command)
        self.assertIn('OPatch/opatch apply -silent "$PATCH_TOP"', steps[0].command)
        self.assertIn("Validating OJVM patch $OJVM_PATCH_ID in DB home patch list.", steps[0].command)
        self.assertIn("OPatch/opatch lspatches", steps[0].command)
        self.assertIn("is not visible in DB home patch list after apply", steps[0].command)

    def test_patch_inventory_collects_versions_and_patch_lists(self):
        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        command = patch_inventory_steps(config)[0].command

        self.assertIn("== Grid home version ==", command)
        self.assertIn("/u01/app/19.0.0/grid/bin/oraversion -compositeVersion", command)
        self.assertIn("== Grid OPatch version ==", command)
        self.assertIn("/u01/app/19.0.0/grid/OPatch/opatch version", command)
        self.assertIn("== Grid patches ==", command)
        self.assertIn("/u01/app/19.0.0/grid/OPatch/opatch lspatches", command)
        self.assertIn("== Database home version ==", command)
        self.assertIn("/u01/app/oracle/product/19.0.0/dbhome_1/bin/oraversion -compositeVersion", command)
        self.assertIn("== Database OPatch version ==", command)
        self.assertIn("/u01/app/oracle/product/19.0.0/dbhome_1/OPatch/opatch version", command)
        self.assertIn("== Database patches ==", command)
        self.assertIn("/u01/app/oracle/product/19.0.0/dbhome_1/OPatch/opatch lspatches", command)
        self.assertNotIn("opatch lsinventory", command)

    def test_create_database_validates_asm_before_dbca(self):
        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        command = create_database_steps(config)[0].command

        self.assertIn("Validating ASM diskgroups before DBCA.", command)
        self.assertIn("Validating Database root script before DBCA.", command)
        self.assertIn("Database root script marker is missing; running root.sh before DBCA.", command)
        self.assertIn("ORACLEASM_ENABLE_IOFILTER=true", command)
        self.assertIn("run prepare-storage-rules again before create-database", command)
        self.assertIn("/u01/app/19.0.0/grid/bin/crsctl check has", command)
        self.assertIn("Oracle Grid Infrastructure HAS is not online; attempting startup before DBCA.", command)
        self.assertIn("/u01/app/19.0.0/grid/bin/crsctl start has", command)
        self.assertIn("/u01/app/19.0.0/grid/bin/asmcmd lsdg", command)
        self.assertIn("ORACLE_HOME=/u01/app/19.0.0/grid", command)
        self.assertIn("/u01/app/19.0.0/grid/bin/sqlplus -L -s / as sysdba", command)
        self.assertIn("v$asm_diskgroup", command)
        self.assertIn("ASM diskgroups are not visible to oracle user through SYSDBA ASM connection", command)
        self.assertIn("ASM diskgroup $diskgroup is missing. Run configure-asm-storage before create-database.", command)
        self.assertIn('sub(/\\/$/, "", name)', command)
        self.assertIn("for diskgroup in DATA RECO", command)
        self.assertIn("Checking for stale partial DBCA database state before createDatabase.", command)
        self.assertIn("srvctl config database -db ORCL", command)
        self.assertIn("ps -eo args=", command)
        self.assertIn("ora_pmon_ORCL", command)
        self.assertIn("Detected running ORCL instance without srvctl registration; validating before DBCA retry.", command)
        self.assertIn("Database ORCL is already queryable; skipping destructive stale cleanup.", command)
        self.assertIn("DB_ALREADY_CREATED=true", command)
        self.assertIn("shutdown abort;", command)
        self.assertIn("asmcmd rm -r +DATA/ORCL", command)
        self.assertIn("ORACLE_HOME=/u01/app/oracle/product/19.0.0/dbhome_1", command)
        self.assertIn("GRID_HOME=/u01/app/19.0.0/grid", command)
        self.assertIn("-storageType ASM -diskGroupName DATA -datafileDestination +DATA -recoveryAreaDestination +RECO", command)
        self.assertIn("/u01/app/oracle/product/19.0.0/dbhome_1/bin/sqlplus -s / as sysdba", command)
        self.assertIn("ALTER DATABASE FORCE LOGGING;", command)
        self.assertIn("ARCHIVE LOG LIST;", command)
        self.assertIn("SELECT name, open_mode, database_role FROM v$database;", command)
        self.assertNotIn("bash -lc \"export ORACLE_SID=ORCL; sqlplus -s / as sysdba <<'SQL'", command)
        self.assertLess(command.index("Validating ASM diskgroups before DBCA."), command.index("dbca -silent -createDatabase"))

    def test_doctor_command_runs(self):
        tmp = self._test_dir("doctor")
        code = main([
            "--report-dir",
            str(tmp),
            "--state-dir",
            str(tmp),
            "doctor",
            "--config",
            "configs/sample-single.json",
            "--dataguard-mode",
            "broker",
        ])

        self.assertIn(code, {0, 1})

    def test_secret_redaction_masks_control_machine_secret_values(self):
        import os

        previous = os.environ.get("ORACLE_AUTO_SYS_PASSWORD")
        os.environ["ORACLE_AUTO_SYS_PASSWORD"] = "UnitTestSecret123"
        try:
            self.assertNotIn("UnitTestSecret123", redact("password=UnitTestSecret123"))
            self.assertIn("***REDACTED***", redact("password=UnitTestSecret123"))
        finally:
            if previous is None:
                os.environ.pop("ORACLE_AUTO_SYS_PASSWORD", None)
            else:
                os.environ["ORACLE_AUTO_SYS_PASSWORD"] = previous

    def test_remote_shell_sources_root_only_secret_file(self):
        command = shell_script("Check secrets", ["test -n \"${ORACLE_AUTO_SYS_PASSWORD:-}\""])

        self.assertIn("/etc/oracle-auto/secrets.env", command)
        self.assertIn("sudo -n bash -lc", command)

    def test_remote_marker_uses_sudo_for_stage_state(self):
        config = load_config(Path("configs/sample-single.json"))
        command = prepare_os_steps(config)[0].command

        self.assertIn("sudo -n test -f /u01/stage/oracle-auto/state/prepare_os/prepare_os.done", command)
        self.assertIn("sudo -n mkdir -p /u01/stage/oracle-auto/state/prepare_os", command)
        self.assertIn("sudo -n tee /u01/stage/oracle-auto/state/prepare_os/prepare_os.done", command)
        self.assertIn("ORACLE_AUTO_NO_REMOTE_RESUME", command)

    def test_no_resume_bypasses_remote_marker(self):
        config = load_config(Path("configs/sample-single.json"))
        step = prepare_os_steps(config)[0]
        command = _with_remote_resume_override([step])[0].command

        self.assertTrue(command.startswith("ORACLE_AUTO_NO_REMOTE_RESUME=1 "))
        self.assertIn("Remote marker bypass requested; rerunning", command)

    def test_prepare_os_sets_grid_and_oracle_profiles(self):
        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        primary_command = prepare_os_steps(config)[0].command
        standby_command = prepare_os_steps(config)[1].command

        self.assertIn("# BEGIN ORACLE-AUTO GRID PROFILE", primary_command)
        self.assertIn("export ORACLE_HOME=/u01/app/19.0.0/grid", primary_command)
        self.assertIn("export PATH=$ORACLE_HOME/bin:$DB_HOME/bin:$PATH", primary_command)
        self.assertIn("export ORACLE_SID=+ASM", primary_command)
        self.assertIn("# BEGIN ORACLE-AUTO ORACLE PROFILE", primary_command)
        self.assertIn("export ORACLE_HOME=/u01/app/oracle/product/19.0.0/dbhome_1", primary_command)
        self.assertIn("export PATH=$ORACLE_HOME/bin:$GRID_HOME/bin:$PATH", primary_command)
        self.assertIn("export ORACLE_SID=ORCL", primary_command)
        self.assertIn("export ORACLE_SID=ORCLSTBY", standby_command)
        self.assertIn("useradd -g oinstall -G asmadmin,asmdba,asmoper,dba,racdba grid", primary_command)
        self.assertIn("usermod -aG asmadmin,asmdba,asmoper,dba,racdba grid", primary_command)

    def test_inventory_remains_remote_read_only(self):
        config = load_config(Path("configs/sample-single.json"))
        command = inventory_steps(config)[0].command

        self.assertNotIn("oracle-auto remote marker wrapper", command)
        self.assertNotIn("/u01/stage/oracle-auto/state", command)

    def test_secret_precheck_uses_sudo_secret_file(self):
        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        command = _secret_env_check(config)

        self.assertIn("sudo -n bash -lc", command)
        self.assertIn("/etc/oracle-auto/secrets.env", command)
        self.assertIn("ORACLE_AUTO_DG_PASSWORD", command)


if __name__ == "__main__":
    unittest.main()
