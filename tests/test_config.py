import unittest
import json
import uuid
from pathlib import Path

from oracle_auto.config import ConfigError, load_config


class ConfigTest(unittest.TestCase):
    def _write_config(self, name: str, data: dict) -> Path:
        path = Path(".test-tmp") / f"{name}-{uuid.uuid4().hex}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

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
        self.assertIsNotNone(config.installer.grid_patch)
        self.assertIsNotNone(config.installer.db_patch)
        self.assertIsNotNone(config.installer.ojvm_patch)
        assert config.installer.grid_patch is not None
        assert config.installer.db_patch is not None
        assert config.installer.ojvm_patch is not None
        self.assertEqual(config.installer.grid_patch.file, "p19_30_grid_ru_Linux-x86-64.zip")
        self.assertEqual(config.installer.db_patch.file, "p19_30_db_ru_Linux-x86-64.zip")
        self.assertEqual(config.installer.ojvm_patch.file, "p19_30_ojvm_ru_Linux-x86-64.zip")

    def test_duplicate_public_ip_rejected(self):
        with self.assertRaises(ConfigError):
            load_config(Path("tests/fixtures/bad-replication-without-standby.json"))

    def test_rac_requires_two_nodes(self):
        with self.assertRaises(ConfigError):
            load_config(Path("tests/fixtures/bad-rac-single-node.json"))

    def test_duplicate_private_or_vip_ip_rejected(self):
        data = json.loads(Path("configs/sample-rac-dg.json").read_text(encoding="utf-8"))
        data["primary_site"]["nodes"][1]["private_ip"] = data["primary_site"]["nodes"][0]["private_ip"]

        with self.assertRaises(ConfigError):
            load_config(self._write_config("duplicate-private", data))

    def test_normal_redundancy_requires_two_disks_per_group(self):
        data = json.loads(Path("configs/sample-single.json").read_text(encoding="utf-8"))
        data["asm"]["redundancy"] = "NORMAL"

        with self.assertRaises(ConfigError):
            load_config(self._write_config("bad-normal-asm", data))


if __name__ == "__main__":
    unittest.main()
