import subprocess
import unittest
from unittest.mock import patch

from oracle_auto.config import NodeConfig, SSHConfig
from oracle_auto.executor import SSHExecutor


class SSHExecutorTest(unittest.TestCase):
    def test_timeout_returns_command_result(self):
        executor = SSHExecutor(SSHConfig(user="oracle_auto"))
        node = NodeConfig(host="db1.example.com", public_ip="192.0.2.10")

        with patch("oracle_auto.executor.subprocess.run") as run:
            run.side_effect = subprocess.TimeoutExpired(
                cmd=["ssh", "oracle_auto@db1.example.com", "sleep 120"],
                timeout=60,
                output="partial stdout",
                stderr="partial stderr",
            )
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

        with patch("oracle_auto.executor.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=["ssh"],
                returncode=0,
                stdout="ok",
                stderr="",
            )
            executor.run(node, "true", timeout=None)

        self.assertIsNone(run.call_args.kwargs["timeout"])


if __name__ == "__main__":
    unittest.main()
