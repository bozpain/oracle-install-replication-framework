import unittest
import tempfile
import threading
import uuid
from pathlib import Path

from oracle_auto.automation import AutomationRunner, AutomationStep, shell_script
from oracle_auto.cli import main
from oracle_auto.config import NodeConfig, load_config
from oracle_auto.executor import CommandResult
from oracle_auto.precheck import _secret_env_check
from oracle_auto.progress import render_progress_line
from oracle_auto.secrets import redact
from oracle_auto.phase_builders.inventory import inventory_steps
from oracle_auto.phase_builders.installer import verify_installer_steps
from oracle_auto.phase_builders.os import prepare_os_steps
from oracle_auto.phase_builders.storage import prepare_storage_rules_steps
from oracle_auto.phase_builders.grid import install_grid_steps
from oracle_auto.phase_builders.database import install_db_software_steps
from oracle_auto.phase_builders.patching import apply_ojvm_patch_steps
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

        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
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

        self.assertEqual(code, 0)
        self.assertIn("RUN   generate-report:local:generate_report", buffer.getvalue())

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

        self.assertIn("Integrity check: LINUX.X64_193000_grid_home.zip", command)
        self.assertIn("Integrity check: p19_30_ojvm_ru_Linux-x86-64.zip", command)
        self.assertIn("Content check: gridSetup.sh", command)
        self.assertNotIn("grep -q 'gridSetup.sh'", command)

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

    def test_precheck_warns_when_asmlib_packages_are_not_visible(self):
        from oracle_auto.precheck import PrecheckRunner

        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        runner = PrecheckRunner(config, executor=None, state=NoopStateStore())
        checks = {check.name: check for check in runner._checks_for(config.primary_site.nodes[0])}

        self.assertIn("asmlib_packages", checks)
        self.assertIn("dnf list oracleasm-support", checks["asmlib_packages"].command)
        self.assertIn("Oracle ASMLIB v3 RPM URL", checks["asmlib_packages"].command)
        self.assertTrue(checks["asmlib_packages"].warn_only)

    def test_precheck_dnf_checks_have_no_framework_timeout(self):
        from oracle_auto.precheck import PrecheckRunner

        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        runner = PrecheckRunner(config, executor=None, state=NoopStateStore())
        checks = {check.name: check for check in runner._checks_for(config.primary_site.nodes[0])}

        self.assertIsNone(checks["oracle_yum_repo"].timeout)
        self.assertIsNone(checks["preinstall_package"].timeout)
        self.assertIsNone(checks["asmlib_packages"].timeout)

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
        db_command = install_db_software_steps(config)[0].command

        self.assertIn("Resetting unconfigured Grid home before install", grid_command)
        self.assertIn("Ensuring at least 512 MiB swap for Oracle installer", grid_command)
        self.assertIn("rm -rf /u01/app/19.0.0/grid/OPatch", grid_command)
        self.assertIn("sudo -iu grid /u01/app/19.0.0/grid/OPatch/opatch version", grid_command)
        self.assertIn("sudo -iu grid env CV_ASSUME_DISTID=OL7", grid_command)
        self.assertIn("ORACLE_BASE=/u01/app/grid /u01/app/19.0.0/grid/gridSetup.sh", grid_command)
        self.assertIn("ASMSNMP_PASSWORD=", grid_command)
        self.assertIn("chmod 600 /u01/stage/responses/grid-site-a.rsp", grid_command)
        self.assertIn("GRID_SETUP_LOG=/u01/stage/logs/gridSetup-site-a.out", grid_command)
        self.assertIn("Successfully Setup Software|execute the following script|executeConfigTools", grid_command)
        self.assertIn("Grid software setup completed; root scripts and config tools will run in following steps.", grid_command)
        self.assertIn("Grid software already installed; skipping software setup and continuing with root scripts/config tools.", grid_command)
        self.assertNotIn("asmcmd afd_label", grid_command)
        self.assertIn("p19_30_grid_ru_Linux-x86-64.zip", grid_command)
        self.assertIn("chmod a+rx /u01/stage /u01/stage/patches /u01/stage/patches/p19_30_grid_ru_linux_x86_64_zip", grid_command)
        self.assertIn("chmod -R a+rX /u01/stage/patches/p19_30_grid_ru_linux_x86_64_zip", grid_command)
        self.assertIn('sudo -iu grid ls -ld "$GRID_PATCH_TOP"', grid_command)
        self.assertNotIn("sudo -iu grid env ORACLE_HOME=/u01/app/19.0.0/grid ORACLE_BASE=/tmp", grid_command)
        self.assertIn('-applyRU "$GRID_PATCH_TOP"', grid_command)
        self.assertIn("/u01/app/oraInventory/orainstRoot.sh", root_command)
        self.assertIn("/u01/app/19.0.0/grid/root.sh", root_command)
        self.assertIn("/u01/app/19.0.0/grid/bin/crsctl check crs", root_command)
        self.assertIn("sudo -iu grid asmcmd lsdg || true", root_command)
        self.assertIn("/u01/app/19.0.0/grid/bin/crsctl check has", config_tools_command)
        self.assertIn("/u01/app/19.0.0/grid/bin/asmcmd lsdg", config_tools_command)
        self.assertIn("Repairing unexpected root-owned Grid image files before executeConfigTools", config_tools_command)
        self.assertIn("find /u01/app/19.0.0/grid -xdev -user root ! -perm /6000 -exec chown grid:oinstall {} +", config_tools_command)
        self.assertIn("-executeConfigTools -responseFile /u01/stage/responses/grid-site-a.rsp -silent", config_tools_command)
        self.assertIn("Grid configuration tools appear complete; skipping executeConfigTools.", config_tools_command)
        self.assertIn("p19_30_db_ru_Linux-x86-64.zip", db_command)
        self.assertIn("Ensuring at least 512 MiB swap for Oracle installer", db_command)
        self.assertIn("sudo -iu oracle env CV_ASSUME_DISTID=OL7", db_command)
        self.assertIn("chmod -R a+rX /u01/stage/patches/p19_30_db_ru_linux_x86_64_zip", db_command)
        self.assertIn('-applyRU "$DB_PATCH_TOP"', db_command)

    def test_final_oracleasm_paths_are_used_without_extra_symlinks(self):
        config = load_config(Path("configs/gcp-single-gi-lab.json"))
        command = prepare_storage_rules_steps(config)[0].command

        self.assertIn("test -b /dev/oracleasm/data1", command)
        self.assertIn("test -b /dev/oracleasm/reco1", command)
        self.assertIn("chown -h grid:asmdba /dev/oracleasm/data1", command)
        self.assertIn("sudo -iu grid test -r /dev/oracleasm/data1", command)
        self.assertIn("oracleasm configure -u grid -g asmdba -e -s y -m 2048", command)
        self.assertIn("Removing unmanaged ASM symlink", command)
        self.assertIn("systemctl restart oracleasm || oracleasm init", command)
        self.assertIn("oracleasm status || true", command)
        self.assertLess(
            command.index("oracleasm querydisk DATA1"),
            command.rindex("sudo -iu grid test -r /dev/oracleasm/data1"),
        )
        self.assertIn("ASMLIB v3 kernel interface: UEK driverless/io_uring", command)
        self.assertIn("/boot/vmlinuz-5.15.0-320.202.8.2.el8uek.x86_64", command)
        self.assertIn("config-manager --set-enabled ol8_addons", command)
        self.assertIn("/u01/sources/oracleasmlib-3.1.1-1.el8.x86_64.rpm", command)
        self.assertNotIn("download.oracle.com/otn_software/asmlib", command)
        self.assertIn("oracleasm createdisk DATA1", command)
        self.assertIn("oracleasm listdisks", command)
        self.assertNotIn("/dev/oracleasm-src/", command)
        self.assertNotIn("ln -sfn", command)

    def test_single_gi_grid_response_omits_cluster_only_fields(self):
        config = load_config(Path("configs/sample-single.json"))
        response = grid_response(config, config.primary_site)

        self.assertIn("oracle.install.option=HA_CONFIG", response)
        self.assertIn("oracle.install.asm.SYSASMPassword=$ASMSNMP_PASSWORD", response)
        self.assertIn("oracle.install.asm.monitorPassword=$ASMSNMP_PASSWORD", response)
        self.assertIn("oracle.install.asm.diskGroup.disks=ORCL:DATA01", response)
        self.assertIn("oracle.install.asm.diskGroup.diskDiscoveryString=ORCL:*", response)
        self.assertIn("oracle.install.asm.configureAFD=false", response)
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
