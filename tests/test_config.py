import unittest
from pathlib import Path

from oracle_auto.config import ConfigError, load_config


class ConfigTest(unittest.TestCase):
    def test_load_single_gi_config(self):
        config = load_config(Path("configs/sample-single.json"))

        self.assertEqual(config.install_type, "single-gi")
        self.assertEqual(config.sources_path, "/u01/sources")
        self.assertTrue(config.active_dataguard_enabled)
        self.assertEqual(config.primary_site.nodes[0].host, "db1-site-a.example.com")
        self.assertEqual(config.primary_site.nodes[0].vip_hostname, "db1-site-a-vip.example.com")
        self.assertEqual(config.primary_site.nodes[0].private_hostname, "db1-site-a-priv.example.com")
        self.assertEqual(config.os.selinux_mode, "permissive")
        self.assertEqual(config.os.ntp_servers, ["192.168.113.41", "192.168.115.41"])
        self.assertEqual(config.asm.data_disks[0].dm_uuid, "mpath-360060e8008a3cf000050a3cf00000102")
        self.assertEqual(config.asm.data_disks[0].symlink_path("DATA", 1), "/dev/oracleasm/data01")

    def test_duplicate_public_ip_rejected(self):
        with self.assertRaises(ConfigError):
            load_config(Path("tests/fixtures/bad-replication-without-standby.json"))

    def test_rac_requires_two_nodes(self):
        with self.assertRaises(ConfigError):
            load_config(Path("tests/fixtures/bad-rac-single-node.json"))


if __name__ == "__main__":
    unittest.main()
