import unittest
from pathlib import Path

from oracle_auto.config import ConfigError, load_config


class ConfigTest(unittest.TestCase):
    def test_load_single_config(self):
        config = load_config(Path("configs/sample-single.json"))

        self.assertEqual(config.install_type, "single-db")
        self.assertEqual(config.sources_path, "/u01/sources")
        self.assertFalse(config.replication.enabled)
        self.assertEqual(config.primary_site.nodes[0].host, "db1-site-a.example.com")

    def test_replication_requires_standby(self):
        with self.assertRaises(ConfigError):
            load_config(Path("tests/fixtures/bad-replication-without-standby.json"))

    def test_rac_requires_two_nodes(self):
        with self.assertRaises(ConfigError):
            load_config(Path("tests/fixtures/bad-rac-single-node.json"))


if __name__ == "__main__":
    unittest.main()
