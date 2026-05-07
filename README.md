# Oracle Install Replication Framework

Automation framework untuk instalasi Oracle Grid Infrastructure, ASM, Oracle Database, patching, dan Active Data Guard.

Baseline saat ini:

- Python `3.12`.
- Oracle Linux `8.10`.
- Install type: `single-gi` atau `rac`.
- Semua deployment memakai Grid Infrastructure dan ASM.
- Active Data Guard otomatis aktif jika `standby_site` diisi.
- Default Data Guard protection mode: `max_performance`.
- SELinux: `permissive`.
- Chrony NTP default: `192.168.113.41` dan `192.168.115.41`.
- Installer ZIP dan patch ZIP disalin manual ke target, default `/u01/sources`.

## Quick Start

Validasi config:

```bash
python main.py validate-config --config configs/sample-rac-dg.json
```

Lihat command precheck tanpa eksekusi remote:

```bash
python main.py precheck --config configs/sample-rac-dg.json --dry-run
```

Jalankan precheck ke server target:

```bash
python main.py precheck --config configs/sample-rac-dg.json
```

## Deployment Commands

Semua command utama mendukung `--dry-run`, `--no-resume`, `--json`, dan `--continue-on-fail`.

Urutan baseline:

```bash
python main.py prepare-os --config configs/sample-rac-dg.json --dry-run
python main.py verify-installer --config configs/sample-rac-dg.json --dry-run
python main.py prepare-storage --config configs/sample-rac-dg.json --dry-run
python main.py install-grid --config configs/sample-rac-dg.json --dry-run
python main.py install-db-software --config configs/sample-rac-dg.json --dry-run
python main.py apply-patch --config configs/sample-rac-dg.json --dry-run
python main.py create-database --config configs/sample-rac-dg.json --dry-run
python main.py setup-active-dataguard --config configs/sample-rac-dg.json --dry-run
python main.py setup-dataguard-broker --config configs/sample-rac-dg.json --dry-run
python main.py validate-deployment --config configs/sample-rac-dg.json --dry-run
```

Role operation:

```bash
python main.py switchover --config configs/sample-rac-dg.json --dry-run
python main.py failover --config configs/sample-rac-dg.json --dry-run
```

Failover sungguhan membutuhkan guardrail:

```bash
python main.py failover --config configs/sample-rac-dg.json --yes
```

Generate report HTML dari state:

```bash
python main.py generate-report --config configs/sample-rac-dg.json
```

Report default disimpan di:

```text
.oracle-auto/reports/<run_id>.html
```

Untuk file YAML, install optional dependency `PyYAML` terlebih dahulu. File JSON bisa langsung dipakai tanpa dependency tambahan.
