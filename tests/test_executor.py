import subprocess
import unittest
from io import StringIO
from contextlib import redirect_stdout
from unittest.mock import patch

from oracle_auto.config import NodeConfig, SSHConfig
from oracle_auto.executor import SSHExecutor


class SSHExecutorTest(unittest.TestCase):
    def test_timeout_returns_command_result(self):
        executor = SSHExecutor(SSHConfig(user="oracle_auto"))
        node = NodeConfig(host="db1.example.com", public_ip="192.0.2.10")

        class TimedOutProcess:
            def __init__(self):
                self.stdout = StringIO("partial stdout\n")
                self.stderr = StringIO("partial stderr\n")

            def wait(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired(cmd=["ssh"], timeout=timeout)
                return 124

            def kill(self):
                pass

        with patch("oracle_auto.executor.subprocess.Popen", return_value=TimedOutProcess()):
            result = executor.run(node, "sleep 120", timeout=60)

        self.assertEqual(result.host, "db1.example.com")
        self.assertEqual(result.returncode, 124)
        self.assertIn("timed out after 60 seconds", result.stderr)
        self.assertIn("partial stderr", result.stderr)
        self.assertEqual(result.stdout, "partial stdout")
        self.assertFalse(result.ok)

    def test_build_ssh_command_enables_keepalive(self):
        executor = SSHExecutor(SSHConfig(user="oracle_auto"))

        command = executor._build_ssh_command("oracle_auto@db1.example.com", "true")

        self.assertIn("ServerAliveInterval=30", command)
        self.assertIn("ServerAliveCountMax=6", command)

    def test_none_timeout_is_passed_through(self):
        executor = SSHExecutor(SSHConfig(user="oracle_auto"))
        node = NodeConfig(host="db1.example.com", public_ip="192.0.2.10")

        class CompletedProcess:
            def __init__(self):
                self.stdout = StringIO("ok\n")
                self.stderr = StringIO("")
                self.timeout_seen = "unset"

            def wait(self, timeout=None):
                self.timeout_seen = timeout
                return 0

        process = CompletedProcess()
        with patch("oracle_auto.executor.subprocess.Popen", return_value=process):
            executor.run(node, "true", timeout=None)

        self.assertIsNone(process.timeout_seen)

    def test_output_streams_while_command_runs(self):
        executor = SSHExecutor(SSHConfig(user="oracle_auto"))
        node = NodeConfig(host="db1.example.com", public_ip="192.0.2.10")

        class CompletedProcess:
            def __init__(self):
                self.stdout = StringIO("line one\nline two\n")
                self.stderr = StringIO("")

            def wait(self, timeout=None):
                return 0

        buffer = StringIO()
        with patch("oracle_auto.executor.subprocess.Popen", return_value=CompletedProcess()):
            with redirect_stdout(buffer):
                result = executor.run(node, "true", timeout=None)

        self.assertEqual(result.stdout, "line one\nline two")
        self.assertEqual(buffer.getvalue(), "line one\nline two\n")


if __name__ == "__main__":
    unittest.main()
