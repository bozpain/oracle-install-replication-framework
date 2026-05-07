import unittest
import uuid
from pathlib import Path

from oracle_auto.cli import main
from oracle_auto.config import load_config
from oracle_auto.phase_builders.storage import prepare_storage_rules_steps


class CliTest(unittest.TestCase):
    def _test_dir(self, name: str) -> Path:
        path = Path(".test-tmp") / f"{name}-{uuid.uuid4().hex}"
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

    def test_storage_guardrail_blocks_real_execution(self):
        code = main([
            "configure-asm-storage",
            "--config",
            "configs/sample-rac-dg.json",
        ])

        self.assertEqual(code, 2)

    def test_storage_rules_contain_dm_uuid_rule(self):
        config = load_config(Path("configs/sample-rac-dg.json"))
        command = prepare_storage_rules_steps(config)[0].command

        self.assertIn('ENV{DM_UUID}=="mpath-360060e8008a3cf000050a3cf00000101"', command)
        self.assertIn('SYMLINK+="oracleasm/ocr01"', command)

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


if __name__ == "__main__":
    unittest.main()
