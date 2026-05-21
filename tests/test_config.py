import unittest
import json
import tempfile
import uuid
import os
from pathlib import Path

from oracle_auto.config import ConfigError, load_config, network_interface_list_for_site


class ConfigTest(unittest.TestCase):
    def _write_config(self, name: str, data: dict) -> Path:
        fd, raw_path = tempfile.mkstemp(prefix=f"oracle-auto-{name}-{uuid.uuid4().hex}-", suffix=".json")
        os.close(fd)
        path = Path(raw_path)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_load_single_gi_config(self):
        config = load_config(Path("configs/single-gi-multisite.json"))

        self.assertEqual(config.install_type, "single-gi")
        self.assertEqual(config.sources_path, "/u01/sources")
        self.assertTrue(config.active_dataguard_enabled)
        self.assertEqual(config.primary_site.nodes[0].host, "ora-primary-01")
        self.assertEqual(config.primary_site.nodes[0].vip_hostname, "ora-primary-01-vip")
        self.assertEqual(config.primary_site.nodes[0].private_hostname, "ora-primary-01-priv")
        self.assertEqual(config.os.selinux_mode, "permissive")
        self.assertEqual(config.os.ntp_servers, ["169.254.169.254"])
        self.assertEqual(config.installer.grid_zip, "LINUX.X64_193000_grid_home.zip")
        self.assertEqual(config.installer.db_zip, "LINUX.X64_193000_db_home.zip")
        self.assertEqual(config.os.asmlib_rpms["x86_64"], "oracleasmlib-3.1.1-1.el8.x86_64.rpm")
        self.assertEqual(list(config.os.asmlib_rpms), ["x86_64"])
        self.assertIsNone(config.dataguard.configuration_method)
        self.assertEqual(config.dataguard.standby_redo_log_size, "200M")
        self.assertEqual(config.asm.ocr_disks, [])
        self.assertEqual(
            config.asm.data_disks[0].source_path,
            "/dev/disk/by-id/scsi-0Google_PersistentDisk_site-a-data-1-part1",
        )
        self.assertEqual(
            config.asm.data_disks[0].final_path("DATA", 1),
            "/dev/disk/by-id/scsi-0Google_PersistentDisk_site-a-data-1-part1",
        )
        self.assertIsNotNone(config.installer.grid_patch)
        self.assertIsNotNone(config.installer.db_patch)
        self.assertIsNotNone(config.installer.ojvm_patch)
        assert config.installer.grid_patch is not None
        assert config.installer.db_patch is not None
        assert config.installer.ojvm_patch is not None
        self.assertEqual(config.installer.grid_patch.file, "p_gi_19.30_linux_x86-64.zip")
        self.assertEqual(config.installer.db_patch.file, "p_dbru_19.30_linux_x86-64.zip")
        self.assertEqual(config.installer.ojvm_patch.file, "p_ojvm_19.30_linux_x86-64.zip")
        self.assertEqual(config.installer.grid_patch.patch_id, "38629535")
        self.assertEqual(config.installer.db_patch.patch_id, "38632161")
        self.assertEqual(config.installer.ojvm_patch.patch_id, "38523609")
        self.assertIsNotNone(config.installer.patch_manifest)
        assert config.installer.patch_manifest is not None
        self.assertEqual(config.installer.patch_manifest.patch_id, "19.30")

    def test_rac_network_interface_list_defaults_to_eth0_eth1(self):
        config = load_config(Path("configs/rac-multisite.json"))

        self.assertIsNone(config.primary_site.network_interface_list)
        self.assertEqual(network_interface_list_for_site(config.primary_site), "eth0:10.148.0.0/24:1,eth1:192.168.10.0/24:5")
        self.assertIsNone(config.primary_site.nodes[0].public_subnet_anchor_ip)
        self.assertIsNone(config.primary_site.nodes[0].private_subnet_anchor_ip)
        assert config.standby_site is not None
        self.assertIsNone(config.standby_site.network_interface_list)
        self.assertEqual(network_interface_list_for_site(config.standby_site), "eth0:10.148.0.0/24:1,eth1:192.168.10.0/24:5")
        self.assertIsNone(config.standby_site.nodes[0].public_subnet_anchor_ip)
        self.assertIsNone(config.standby_site.nodes[0].private_subnet_anchor_ip)

    def test_bad_network_interface_list_is_rejected(self):
        data = json.loads(Path("configs/rac-multisite.json").read_text(encoding="utf-8"))
        data["primary_site"]["network_interface_list"] = "eth0:10.148.0.0"

        with self.assertRaisesRegex(ConfigError, "interface:subnet:type"):
            load_config(self._write_config("bad-network-interface-list", data))

    def test_duplicate_public_ip_rejected(self):
        with self.assertRaises(ConfigError):
            load_config(Path("tests/fixtures/bad-replication-without-standby.json"))

    def test_rac_requires_two_nodes(self):
        with self.assertRaises(ConfigError):
            load_config(Path("tests/fixtures/bad-rac-single-node.json"))

    def test_duplicate_private_or_vip_ip_rejected(self):
        data = json.loads(Path("configs/rac-multisite.json").read_text(encoding="utf-8"))
        data["primary_site"]["nodes"][1]["private_ip"] = data["primary_site"]["nodes"][0]["private_ip"]

        with self.assertRaises(ConfigError):
            load_config(self._write_config("duplicate-private", data))

    def test_high_redundancy_requires_three_disks_per_group(self):
        data = json.loads(Path("configs/single-gi-multisite.json").read_text(encoding="utf-8"))
        data["asm"]["redundancy"] = "HIGH"

        with self.assertRaises(ConfigError):
            load_config(self._write_config("bad-high-asm", data))

    def test_single_gi_rejects_ocr_disks(self):
        data = json.loads(Path("configs/single-gi-multisite.json").read_text(encoding="utf-8"))
        data["asm"]["sites"]["site-a"]["ocr"] = [
            {
                "name": "OCR01",
                "path": "/dev/disk/by-id/scsi-0Google_PersistentDisk_site-a-ocr-1-part1",
            }
        ]

        with self.assertRaisesRegex(ConfigError, "ocr_disks is only used"):
            load_config(self._write_config("single-with-ocr", data))

    def test_legacy_patch_id_is_parsed_when_configured(self):
        data = json.loads(Path("configs/single-gi-multisite.json").read_text(encoding="utf-8"))
        data["installer"] = {}
        data["installer"]["grid_zip"] = "LINUX.X64_193000_grid_home.zip"
        data["installer"]["db_zip"] = "LINUX.X64_193000_db_home.zip"
        data["installer"]["opatch_zip"] = "p6880880_190000_Linux-x86-64.zip"
        data["installer"]["grid_patch"] = {
            "name": "Legacy Grid RU",
            "type": "ru",
            "file": "legacy_grid_ru.zip",
            "patch_id": "37642901",
        }

        config = load_config(self._write_config("patch-id", data))

        assert config.installer.grid_patch is not None
        self.assertEqual(config.installer.grid_patch.patch_id, "37642901")

    def test_site_specific_asm_paths_are_parsed(self):
        config = load_config(Path("configs/single-gi-multisite.json"))
        primary = config.primary_site.nodes[0]
        standby = config.standby_site.nodes[0]

        assert config.standby_site is not None
        self.assertEqual(
            config.asm.data_disks[0].path_for(site_name=config.primary_site.name, node_host=primary.host),
            "/dev/disk/by-id/scsi-0Google_PersistentDisk_site-a-data-1-part1",
        )
        self.assertEqual(
            config.asm.data_disks[0].path_for(site_name=config.standby_site.name, node_host=standby.host),
            "/dev/disk/by-id/scsi-0Google_PersistentDisk_site-b-data-1-part1",
        )

    def test_asm_disk_id_serial_and_id_wwn_are_parsed(self):
        data = json.loads(Path("configs/single-gi-multisite.json").read_text(encoding="utf-8"))
        data["asm"].pop("sites")
        data["asm"]["data_disks"] = [{"id_serial": "scsi-3600ABCDEF001", "name": "DATA01"}]
        data["asm"]["reco_disks"] = [{"ID_WWN": "0x600abcdef002", "name": "RECO01"}]

        config = load_config(self._write_config("asm-byid", data))

        data_disk = config.asm.data_disks[0]
        reco_disk = config.asm.reco_disks[0]
        self.assertEqual(data_disk.id_serial, "scsi-3600ABCDEF001")
        self.assertEqual(data_disk.source_for(), "ID_SERIAL=scsi-3600ABCDEF001")
        self.assertEqual(data_disk.path_for(), "/dev/disk/by-id/scsi-3600ABCDEF001")
        self.assertEqual(reco_disk.id_wwn, "0x600abcdef002")
        self.assertEqual(reco_disk.source_for(), "ID_WWN=0x600abcdef002")
        self.assertEqual(reco_disk.path_for(), "/dev/disk/by-id/wwn-0x600abcdef002")

    def test_asm_disk_id_serial_rejects_device_path(self):
        data = json.loads(Path("configs/single-gi-multisite.json").read_text(encoding="utf-8"))
        data["asm"].pop("sites")
        data["asm"]["data_disks"] = [{"id_serial": "/dev/disk/by-id/scsi-3600ABCDEF001", "name": "DATA01"}]

        with self.assertRaisesRegex(ConfigError, "id_serial must contain ID_SERIAL only"):
            load_config(self._write_config("bad-id-serial", data))

    def test_dataguard_standby_redo_log_size_is_configurable(self):
        data = json.loads(Path("configs/single-gi-multisite.json").read_text(encoding="utf-8"))
        data["dataguard"] = {"standby_redo_log_size": "512m"}

        config = load_config(self._write_config("dg-redo-size", data))

        self.assertEqual(config.dataguard.standby_redo_log_size, "512M")

    def test_dataguard_standby_redo_log_size_rejects_bad_value(self):
        data = json.loads(Path("configs/single-gi-multisite.json").read_text(encoding="utf-8"))
        data["dataguard"] = {"standby_redo_log_size": "two gigs"}

        with self.assertRaisesRegex(ConfigError, "standby_redo_log_size"):
            load_config(self._write_config("bad-dg-redo-size", data))

    def test_unknown_site_specific_asm_path_key_is_rejected(self):
        data = json.loads(Path("configs/single-gi-multisite.json").read_text(encoding="utf-8"))
        data["asm"]["sites"]["typo-site"] = {
            "data": [
                {
                    "name": "DATA01",
                    "path": "/dev/disk/by-id/google-data-typo",
                },
                {
                    "name": "DATA02",
                    "path": "/dev/disk/by-id/google-data-typo-2",
                },
            ],
            "reco": [
                {
                    "name": "RECO01",
                    "path": "/dev/disk/by-id/google-reco-typo",
                },
                {
                    "name": "RECO02",
                    "path": "/dev/disk/by-id/google-reco-typo-2",
                },
            ],
        }

        with self.assertRaisesRegex(ConfigError, "unknown site_paths"):
            load_config(self._write_config("unknown-site-path", data))


if __name__ == "__main__":
    unittest.main()
