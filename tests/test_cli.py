import unittest
import tempfile
import uuid
from pathlib import Path

from oracle_auto.cli import main
from oracle_auto.config import load_config
from oracle_auto.secrets import redact
from oracle_auto.phase_builders.storage import prepare_storage_rules_steps
from oracle_auto.phase_builders.grid import install_grid_steps
from oracle_auto.phase_builders.database import install_db_software_steps
from oracle_auto.phase_builders.patching import apply_ojvm_patch_steps


class CliTest(unittest.TestCase):
    def _test_dir(self, name: str) -> Path:
        path = Path(tempfile.gettempdir()) / "oracle-auto-tests" / f"{name}-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=False)
        return path

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

    def test_storage_rules_contain_dm_uuid_rule(self):
        config = load_config(Path("configs/sample-rac-dg.json"))
        command = prepare_storage_rules_steps(config)[0].command

        self.assertIn('ENV{DM_UUID}=="mpath-360060e8008a3cf000050a3cf00000101"', command)
        self.assertIn('SYMLINK+="oracleasm/ocr01"', command)

    def test_install_steps_apply_targeted_ru_patches(self):
        config = load_config(Path("configs/sample-single.json"))
        grid_command = install_grid_steps(config)[0].command
        db_command = install_db_software_steps(config)[0].command

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


if __name__ == "__main__":
    unittest.main()
