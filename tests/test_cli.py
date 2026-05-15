import unittest
import tempfile
import uuid
from pathlib import Path

from oracle_auto.automation import AutomationRunner, AutomationStep, shell_script
from oracle_auto.cli import main
from oracle_auto.config import NodeConfig, load_config
from oracle_auto.executor import CommandResult
from oracle_auto.precheck import _secret_env_check
from oracle_auto.secrets import redact
from oracle_auto.phase_builders.inventory import inventory_steps
from oracle_auto.phase_builders.os import prepare_os_steps
from oracle_auto.phase_builders.storage import prepare_storage_rules_steps
from oracle_auto.phase_builders.grid import install_grid_steps
from oracle_auto.phase_builders.database import install_db_software_steps
from oracle_auto.phase_builders.patching import apply_ojvm_patch_steps


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
        ])

        self.assertEqual(code, 0)
        self.assertTrue((tmp / "rac-adg-demo-plan.html").exists())
        self.assertTrue((tmp / "rac-adg-demo-plan.json").exists())
        self.assertTrue((tmp / "rac-adg-demo-runbook.sh").exists())
        self.assertTrue((tmp / "rac-adg-demo-phase-runbooks" / "prepare-os.sh").exists())
        self.assertTrue((tmp / "rac-adg-demo-phase-runbooks" / "apply-ojvm-patch.sh").exists())
        self.assertFalse((tmp / "rac-adg-demo-phase-runbooks" / "datapatch.sh").exists())

    def test_storage_guardrail_blocks_real_execution(self):
        code = main([
            "configure-asm-storage",
            "--config",
            "configs/sample-rac-dg.json",
        ])

        self.assertEqual(code, 2)

    def test_full_guardrail_blocks_real_execution_without_flags(self):
        code = main([
            "full",
            "--config",
            "configs/sample-single.json",
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
            "--dry-run",
        ])

        self.assertEqual(code, 0)
        self.assertTrue((tmp / "single-gi-demo.html").exists())

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

    def test_storage_rules_contain_dm_uuid_rule(self):
        config = load_config(Path("configs/sample-rac-dg.json"))
        command = prepare_storage_rules_steps(config)[0].command

        self.assertIn('ENV{DM_UUID}=="mpath-360060e8008a3cf000050a3cf00000101"', command)
        self.assertIn('SYMLINK+="oracleasm/ocr01"', command)

    def test_precheck_storage_inspection_uses_sudo(self):
        from oracle_auto.precheck import _disk_signature_check, _disk_size_check

        config = load_config(Path("configs/gcp-single-gi-lab.json"))

        self.assertIn("sudo -n wipefs", _disk_signature_check(config))
        self.assertIn("sudo -n blockdev", _disk_size_check(config))

    def test_install_steps_apply_targeted_ru_patches(self):
        config = load_config(Path("configs/sample-single.json"))
        grid_command = install_grid_steps(config)[0].command
        db_command = install_db_software_steps(config)[0].command

        self.assertIn("asmcmd afd_label DATA01 /dev/oracleasm/data01 --init", grid_command)
        self.assertLess(grid_command.index("asmcmd afd_label DATA01"), grid_command.index("gridSetup.sh -silent"))
        self.assertIn("p19_30_grid_ru_Linux-x86-64.zip", grid_command)
        self.assertIn('-applyRU "$GRID_PATCH_TOP"', grid_command)
        self.assertIn("p19_30_db_ru_Linux-x86-64.zip", db_command)
        self.assertIn('-applyRU "$DB_PATCH_TOP"', db_command)

    def test_ojvm_patch_runs_before_database_creation_phase(self):
        config = load_config(Path("configs/sample-single.json"))
        steps = apply_ojvm_patch_steps(config)

        self.assertEqual(len(steps), 2)
        self.assertIn("p19_30_ojvm_ru_Linux-x86-64.zip", steps[0].command)
        self.assertIn('OPatch/opatch apply -silent "$PATCH_TOP"', steps[0].command)

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
