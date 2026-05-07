# Oracle Install Replication Framework

Professional automation framework untuk provisioning Oracle Grid Infrastructure, ASM storage, Oracle Database, patching, dan Active Data Guard pada Oracle Linux.

Framework ini dirancang untuk environment fresh install dengan standar operasi yang konsisten: user `grid` dan `oracle` terpisah, semua deployment memakai Grid Infrastructure dan ASM, storage didefinisikan sebagai diskgroup `OCR`, `DATA`, dan `RECO`, serta Active Data Guard aktif otomatis ketika `standby_site` diisi di config.

## Current Baseline

- Python `3.12`.
- Oracle Linux `8.10`.
- Oracle Grid Infrastructure dan Oracle Database `19c`.
- Patch baseline `19.30`, dengan struktur config yang bisa berkembang untuk `19.31`, one-off patch, OJVM, dan patch berikutnya.
- Install type hanya `single-gi` dan `rac`.
- Active Data Guard default `max_performance`.
- Data Guard configuration method: `manual` atau `broker`.
- SELinux otomatis `permissive`.
- Chrony otomatis memakai NTP `192.168.113.41` dan `192.168.115.41`.
- Firewall service seperti `firewalld`, `iptables`, dan `nftables` otomatis dinonaktifkan.
- Installer dan patch ZIP disalin manual ke target server, lalu diverifikasi automation.

## Documentation

Mulai dari dokumen ini untuk overview. Detail teknis dan runbook operator dipisahkan supaya README tetap bersih.

- [Installation and Replication Outline](docs/installation-replication-outline.md): blueprint desain, scope, default, dan roadmap framework.
- [Deployment Guide](docs/deployment_guide.md): panduan deployment paling detail, termasuk config, precheck, dry-run, urutan command, report, switchover, failover, dan troubleshooting.

## Architecture

Framework dibagi menjadi beberapa layer:

- `oracle_auto.config`: schema config, validasi topology, ASM disk, DNS resolver, installer, patch, dan Data Guard method.
- `oracle_auto.precheck`: precheck remote non-destruktif sebelum deployment.
- `oracle_auto.phases`: generator step automation untuk OS, installer, storage, Grid, DB home, patching, database, Active Data Guard, Broker, validation, switchover, dan failover.
- `oracle_auto.automation`: runner generik untuk SSH execution, dry-run, state/resume, dan result normalization.
- `oracle_auto.report`: HTML report generator.
- `oracle_auto.cli`: command-line interface untuk semua workflow.

## Quick Start

Validasi sample RAC + Active Data Guard config:

```bash
python main.py validate-config --config configs/sample-rac-dg.json
```

Lihat precheck tanpa SSH execution:

```bash
python main.py precheck --config configs/sample-rac-dg.json --dry-run
```

Generate report dari state saat ini:

```bash
python main.py generate-report --config configs/sample-rac-dg.json
```

## Deployment Flow

Urutan baseline deployment:

```bash
python main.py validate-config --config configs/sample-rac-dg.json
python main.py precheck --config configs/sample-rac-dg.json --dry-run
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
python main.py generate-report --config configs/sample-rac-dg.json
```

Hapus `--dry-run` hanya setelah config, DNS, disk, installer ZIP, patch ZIP, dan target host sudah siap.

## Operator Controls

Semua command deployment utama mendukung:

- `--dry-run`: tampilkan command tanpa membuka SSH session.
- `--no-resume`: paksa step berjalan ulang walaupun state sudah `done`.
- `--json`: output machine-readable.
- `--continue-on-fail`: lanjutkan step lain meski ada failure.

Failover memiliki guardrail tambahan:

```bash
python main.py failover --config configs/sample-rac-dg.json --yes
```

## State and Reports

State default:

```text
.oracle-auto/state/<run_id>.json
```

HTML report default:

```text
.oracle-auto/reports/<run_id>.html
```

Report tetap dibuat walaupun ada step gagal, sehingga hasil eksekusi bisa direview setelah troubleshooting.

## Validation Status

Baseline saat ini sudah memiliki command structure, dry-run support, state/resume, dan HTML reporting. Command Oracle yang menyentuh installer, GI, ASM/AFD, OPatch, RMAN duplicate, Broker, switchover, dan failover tetap harus divalidasi di lab target karena detail behavior dapat berubah mengikuti layout installer, patch bundle, storage, dan standar environment.

