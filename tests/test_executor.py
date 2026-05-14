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


if __name__ == "__main__":
    unittest.main()
