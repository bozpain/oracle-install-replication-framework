# Deployment Guide

Panduan ini adalah runbook paling detail untuk operator yang akan menjalankan Oracle Install Replication Framework. Ikuti urutan ini dari atas ke bawah ketika menyiapkan deployment baru.

## 1. Prerequisites

Control machine:

- Python `3.12`.
- Akses SSH ke semua target host.
- Repository ini tersedia di control machine.
- Config deployment sudah dibuat dari sample.

Target servers:

- Oracle Linux fresh install.
- SSH root aktif untuk bootstrap.
- DNS record public hostname tersedia.
- Untuk RAC, SCAN DNS record tersedia dan resolve dari resolver yang akan dikonfigurasi.
- Installer dan patch ZIP sudah disalin manual ke target, default `/u01/sources`.
- Disk ASM untuk `OCR`, `DATA`, dan `RECO` sudah terlihat sebagai block device.

## 2. Choose Deployment Type

Pilih salah satu:

- `single-gi`: single node GI + ASM + database.
- `rac`: RAC GI + ASM + database.

Jika standby dibutuhkan, isi `standby_site`. Jika tidak, hapus blok `standby_site`.

Rule penting:

- Primary `single-gi` hanya boleh standby `single-gi`.
- Primary `rac` hanya boleh standby `rac`.
- Jumlah node standby harus sama dengan primary.
- Active Data Guard otomatis aktif jika `standby_site` ada.

## 3. Prepare Config

Mulai dari sample:

```bash
cp configs/sample-rac-dg.json configs/my-deployment.json
```

Minimal review blok berikut:

- `run_id`
- `install_type`
- `version`
- `os`
- `ssh`
- `dns`
- `primary_site`
- `standby_site`
- `asm`
- `installer`
- `dataguard`

## 4. Network Configuration

Isi public hostname dan public IP untuk semua node.

Untuk RAC, isi juga:

- `private_ip`
- `vip_ip`
- `scan_name`

Jangan isi VIP hostname atau private hostname. Framework membuatnya otomatis:

```text
db1.example.com -> db1-vip.example.com
db1.example.com -> db1-priv.example.com
```

SCAN:

- Isi `scan_name`.
- Jangan masukkan SCAN IP ke config.
- Jangan masukkan SCAN ke `/etc/hosts`.
- Pastikan DNS resolver dapat resolve SCAN.

DNS resolver:

```json
"dns": {
  "resolvers": ["192.168.113.10", "192.168.115.10"],
  "search_domains": ["example.com"]
}
```

## 5. ASM Disk Configuration

Isi disk berdasarkan diskgroup:

```json
"asm": {
  "redundancy": "EXTERNAL",
  "ocr_disks": ["/dev/mapper/ocr01", "/dev/mapper/ocr02", "/dev/mapper/ocr03"],
  "data_disks": ["/dev/mapper/data01", "/dev/mapper/data02"],
  "reco_disks": ["/dev/mapper/reco01", "/dev/mapper/reco02"]
}
```

Rule:

- Disk tidak boleh duplikat antar diskgroup.
- Disk harus terlihat di target sebagai block device.
- Untuk RAC, disk shared harus konsisten di semua node.

## 6. Installer and Patch Configuration

Operator menyalin file ZIP manual ke target server.

Contoh:

```json
"installer": {
  "sources_path": "/u01/sources",
  "grid_zip": "LINUX.X64_193000_grid_home.zip",
  "db_zip": "LINUX.X64_193000_db_home.zip",
  "opatch_zip": "p6880880_190000_Linux-x86-64.zip",
  "patches": [
    {
      "name": "19.30 RU",
      "type": "ru",
      "file": "p19_30_ru_Linux-x86-64.zip"
    }
  ]
}
```

Patch list dijalankan sesuai urutan config. Tambahkan patch baru sebagai item baru, bukan mengganti struktur.

## 7. Data Guard Configuration

Jika standby diisi, Active Data Guard otomatis aktif.

Pilih method:

```json
"dataguard": {
  "configuration_method": "broker",
  "protection_mode": "max_performance"
}
```

Pilihan:

- `broker`: recommended untuk manageability.
- `manual`: physical standby tanpa Broker.

Protection mode baseline selalu `max_performance`.

## 8. Validate Config

Jalankan:

```bash
python main.py validate-config --config configs/my-deployment.json
```

Expected result:

- Config valid.
- Install type benar.
- Node count benar.
- ASM diskgroup count benar.
- DNS resolver tampil.
- Active Data Guard enabled/disabled sesuai ada tidaknya standby.

Jika gagal, perbaiki config sebelum lanjut.

## 9. Precheck

Dry-run precheck:

```bash
python main.py precheck --config configs/my-deployment.json --dry-run
```

Remote precheck:

```bash
python main.py precheck --config configs/my-deployment.json
```

Precheck memvalidasi:

- SSH connectivity.
- Oracle Linux version.
- Kernel.
- Package manager.
- Yum/dnf repo.
- Preinstall package.
- DNS resolver.
- Public hostname resolution.
- Installer ZIP files.
- `/u01` capacity.
- User `oracle` dan `grid`.
- Chrony/time sync.
- SELinux.
- ASM disk visibility.
- RAC hostname FQDN.
- SCAN DNS resolution.
- Private interconnect hint.

## 10. Deployment Sequence

Selalu mulai dengan dry-run penuh:

```bash
python main.py prepare-os --config configs/my-deployment.json --dry-run
python main.py verify-installer --config configs/my-deployment.json --dry-run
python main.py prepare-storage --config configs/my-deployment.json --dry-run
python main.py install-grid --config configs/my-deployment.json --dry-run
python main.py install-db-software --config configs/my-deployment.json --dry-run
python main.py apply-patch --config configs/my-deployment.json --dry-run
python main.py create-database --config configs/my-deployment.json --dry-run
python main.py setup-active-dataguard --config configs/my-deployment.json --dry-run
python main.py setup-dataguard-broker --config configs/my-deployment.json --dry-run
python main.py validate-deployment --config configs/my-deployment.json --dry-run
```

Jika dry-run sudah sesuai, jalankan tanpa `--dry-run` sesuai urutan yang sama.

## 11. Command Details

### prepare-os

Menjalankan OS bootstrap:

- Install package baseline.
- Buat group Oracle.
- Buat user `grid` dan `oracle`.
- Buat direktori `/u01`.
- Set ownership.
- Set DNS resolver.
- Update `/etc/hosts`.
- Disable firewall.
- Set SELinux `permissive`.
- Configure chrony.

Command:

```bash
python main.py prepare-os --config configs/my-deployment.json
```

### verify-installer

Memastikan file ZIP tersedia dan bisa dibaca:

- Grid ZIP.
- DB ZIP.
- OPatch ZIP.
- Patch ZIP list.

Command:

```bash
python main.py verify-installer --config configs/my-deployment.json
```

### prepare-storage

Menyiapkan ASM storage:

- Validasi block device.
- Label AFD.
- Create diskgroup `OCR`, `DATA`, `RECO`.
- Validasi ASM diskgroup.

Command:

```bash
python main.py prepare-storage --config configs/my-deployment.json
```

### install-grid

Menjalankan Grid Infrastructure silent install.

Command:

```bash
python main.py install-grid --config configs/my-deployment.json
```

### install-db-software

Menjalankan Oracle Database software silent install.

Command:

```bash
python main.py install-db-software --config configs/my-deployment.json
```

### apply-patch

Mengupdate OPatch dan menjalankan patch list sesuai config.

Command:

```bash
python main.py apply-patch --config configs/my-deployment.json
```

### create-database

Membuat primary database dengan DBCA silent.

Command:

```bash
python main.py create-database --config configs/my-deployment.json
```

### setup-active-dataguard

Menyiapkan Data Guard parameter, password file baseline, RMAN duplicate, dan managed recovery.

Command:

```bash
python main.py setup-active-dataguard --config configs/my-deployment.json
```

### setup-dataguard-broker

Berjalan jika `configuration_method=broker`.

Command:

```bash
python main.py setup-dataguard-broker --config configs/my-deployment.json
```

### validate-deployment

Validasi GI, ASM, database role, service, dan Data Guard metrics.

Command:

```bash
python main.py validate-deployment --config configs/my-deployment.json
```

## 12. Switchover

Dry-run:

```bash
python main.py switchover --config configs/my-deployment.json --dry-run
```

Execute:

```bash
python main.py switchover --config configs/my-deployment.json
```

Setelah switchover, jalankan:

```bash
python main.py validate-deployment --config configs/my-deployment.json
python main.py generate-report --config configs/my-deployment.json
```

## 13. Failover

Failover adalah destructive role operation. Dry-run dulu:

```bash
python main.py failover --config configs/my-deployment.json --dry-run
```

Execute membutuhkan `--yes`:

```bash
python main.py failover --config configs/my-deployment.json --yes
```

Setelah failover:

- Validasi primary baru.
- Review former primary status.
- Rencanakan rebuild atau reinstate.
- Generate report.

## 14. State and Resume

State disimpan di:

```text
.oracle-auto/state/<run_id>.json
```

Default behavior:

- Step yang sudah `done` tidak dijalankan ulang.
- Gunakan `--no-resume` untuk memaksa ulang.

Contoh:

```bash
python main.py verify-installer --config configs/my-deployment.json --no-resume
```

## 15. JSON Output

Gunakan `--json` untuk integrasi pipeline:

```bash
python main.py validate-deployment --config configs/my-deployment.json --json
```

## 16. Continue on Fail

Gunakan hanya untuk audit luas ketika ingin melihat semua failure dalam satu run:

```bash
python main.py precheck --config configs/my-deployment.json --continue-on-fail
```

Untuk install sungguhan, lebih aman berhenti di failure pertama, perbaiki, lalu resume.

## 17. HTML Report

Generate report:

```bash
python main.py generate-report --config configs/my-deployment.json
```

Lokasi:

```text
.oracle-auto/reports/<run_id>.html
```

Report berisi:

- Deployment identity.
- Version baseline.
- Topology.
- Generated private/VIP hostnames.
- DNS resolver.
- SCAN status.
- ASM disk mapping.
- Installer and patch list.
- Execution results.

## 18. Troubleshooting

Config gagal:

- Cek `install_type`.
- Cek node count primary/standby.
- Cek disk duplikat.
- Cek missing `grid_zip` atau `db_zip`.

SCAN gagal resolve:

- Cek DNS resolver.
- Cek `/etc/resolv.conf`.
- Cek record SCAN di DNS.
- Jangan workaround dengan `/etc/hosts`.

Installer verification gagal:

- Pastikan file ZIP ada di `sources_path`.
- Pastikan size tidak `0`.
- Pastikan permission bisa dibaca user target.

ASM disk gagal:

- Cek disk path.
- Cek multipath.
- Cek disk sudah dipakai filesystem atau belum.
- Cek konsistensi disk antar node RAC.

Patch gagal:

- Cek OPatch version.
- Cek patch unzip path.
- Cek conflict check.
- Cek apakah patch harus `opatchauto` atau `opatch`.

Data Guard gagal:

- Cek listener dan service name.
- Cek password file.
- Cek `tnsnames.ora`.
- Cek archive log mode dan force logging.
- Cek network primary ke standby dan sebaliknya.

## 19. Production Readiness Checklist

Sebelum production:

- Config sudah direview DBA dan infra.
- DNS public, private, VIP, dan SCAN sudah siap.
- Disk ASM sudah valid.
- Installer dan patch ZIP sudah benar.
- Dry-run semua command sudah direview.
- Precheck remote bersih dari `FAIL`.
- Backup atau rollback plan tersedia.
- Switchover/failover hanya dijalankan dengan window dan approval.

