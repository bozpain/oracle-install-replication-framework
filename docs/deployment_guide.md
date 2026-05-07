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
- Untuk RAC, hanya SCAN DNS record yang wajib tersedia dan resolve dari resolver yang akan dikonfigurasi.
- Public, private, dan VIP hostname tidak wajib ada di DNS; framework menulis semuanya ke `/etc/hosts`.
- Installer dan patch ZIP sudah disalin manual ke target, default `/u01/sources`.
- Disk ASM untuk `OCR`, `DATA`, dan `RECO` sudah terlihat oleh udev dengan `DM_UUID`.

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
- `secrets`

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
- Public/private/VIP hostnames diabaikan dari DNS validation dan divalidasi sebagai `/etc/hosts` entries.

DNS resolver:

```json
"dns": {
  "resolvers": ["192.168.113.10", "192.168.115.10"],
  "search_domains": ["example.com"]
}
```

## 5. ASM Disk Configuration

Isi disk berdasarkan `DM_UUID`, bukan `/dev/mapper/mpathX` atau `/dev/sdX`. Nama device seperti itu bisa berubah, sedangkan `DM_UUID` multipath stabil untuk rule matching.

```json
"asm": {
  "redundancy": "EXTERNAL",
  "ocr_disks": [
    "360060e8008a3cf000050a3cf00000101",
    "360060e8008a3cf000050a3cf00000102",
    "360060e8008a3cf000050a3cf00000103"
  ],
  "data_disks": [
    "360060e8008a3cf000050a3cf00000104",
    "360060e8008a3cf000050a3cf00000105"
  ],
  "reco_disks": [
    "360060e8008a3cf000050a3cf00000106",
    "360060e8008a3cf000050a3cf00000107"
  ]
}
```

Rule:

- Disk tidak boleh duplikat antar diskgroup.
- UUID boleh ditulis dengan atau tanpa prefix `mpath-`; framework akan menormalisasi menjadi `DM_UUID=mpath-<uuid>`.
- Framework membuat udev rule dan symlink `/dev/oracleasm/...`.
- Default symlink dibuat otomatis: `ocr01`, `data01`, `reco01`, dan seterusnya.
- Jika suatu environment butuh nama khusus seperti `data102`, gunakan object optional:

  ```json
  "data_disks": [
    {
      "uuid": "360060e8008a3cf000050a3cf00000175",
      "name": "data102"
    }
  ]
  ```

- Rule yang dihasilkan kira-kira seperti:

  ```text
  ACTION=="add|change", ENV{DM_UUID}=="mpath-360060e8008a3cf000050a3cf00000175", SYMLINK+="oracleasm/data102", GROUP="asmadmin", OWNER="grid", MODE="0660"
  ```

- Disk harus terlihat oleh `udevadm info --export-db`.
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

## 7.1 Secrets Configuration

Password tidak ditulis hardcoded di script. Config hanya menyimpan nama environment variable yang harus tersedia di target saat step terkait dijalankan:

```json
"secrets": {
  "sys_password_env": "ORACLE_AUTO_SYS_PASSWORD",
  "system_password_env": "ORACLE_AUTO_SYSTEM_PASSWORD",
  "asmsnmp_password_env": "ORACLE_AUTO_ASMSNMP_PASSWORD",
  "dg_password_env": "ORACLE_AUTO_DG_PASSWORD"
}
```

Sebelum `create-database` dan Data Guard step, set secret di target sesuai mekanisme secure environment kamu. Untuk lab sementara, bisa export di session root target sebelum eksekusi.

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

## 8.1 Local Doctor

Jalankan local readiness check di control machine:

```bash
python main.py doctor --config configs/my-deployment.json
```

Doctor mengecek Python version, binary SSH, direktori report/state/log writable, config parseability, dan optional YAML support.

## 8.2 Remote Inventory

Ambil inventory read-only dari target:

```bash
python main.py inventory --config configs/my-deployment.json --dry-run
python main.py inventory --config configs/my-deployment.json
```

Inventory membantu review OS, network, DNS, storage `DM_UUID`, dan isi `/u01/sources` sebelum command yang mengubah server.

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
- `/etc/hosts` entries untuk public/private/VIP.
- Installer ZIP files.
- `/u01` capacity.
- User `oracle` dan `grid`.
- Chrony/time sync.
- SELinux.
- ASM disk DM_UUID visibility.
- RAC hostname FQDN.
- SCAN DNS resolution.
- SCAN DNS record count sebagai warning/informasi.
- Private interconnect hint.

## 10. Deployment Sequence

Selalu mulai dengan dry-run penuh:

```bash
python main.py prepare-os --config configs/my-deployment.json --dry-run
python main.py verify-installer --config configs/my-deployment.json --dry-run
python main.py prepare-storage-rules --config configs/my-deployment.json --dry-run
python main.py install-grid --config configs/my-deployment.json --dry-run
python main.py configure-asm-storage --config configs/my-deployment.json --dry-run
python main.py install-db-software --config configs/my-deployment.json --dry-run
python main.py update-opatch --config configs/my-deployment.json --dry-run
python main.py analyze-patch --config configs/my-deployment.json --dry-run
python main.py apply-grid-patch --config configs/my-deployment.json --dry-run
python main.py apply-db-patch --config configs/my-deployment.json --dry-run
python main.py datapatch --config configs/my-deployment.json --dry-run
python main.py patch-inventory --config configs/my-deployment.json --dry-run
python main.py create-database --config configs/my-deployment.json --dry-run
python main.py setup-active-dataguard --config configs/my-deployment.json --dry-run
python main.py setup-dataguard-broker --config configs/my-deployment.json --dry-run
python main.py validate-deployment --config configs/my-deployment.json --dry-run
```

Jika dry-run sudah sesuai, jalankan tanpa `--dry-run` sesuai urutan yang sama.

## 11. Command Details

Implementation trace:

- `prepare-os`: `oracle_auto/phase_builders/os.py`
- `verify-installer`: `oracle_auto/phase_builders/installer.py`
- `prepare-storage-rules`, `configure-asm-storage`, dan compatibility `prepare-storage`: `oracle_auto/phase_builders/storage.py`
- `install-grid`: `oracle_auto/phase_builders/grid.py`
- `install-db-software` dan `create-database`: `oracle_auto/phase_builders/database.py`
- `update-opatch`, `analyze-patch`, `apply-grid-patch`, `apply-db-patch`, `datapatch`, `patch-inventory`, dan wrapper `apply-patch`: `oracle_auto/phase_builders/patching.py`
- `setup-active-dataguard` dan `setup-dataguard-broker`: `oracle_auto/phase_builders/dataguard.py`
- `validate-deployment`: `oracle_auto/phase_builders/validation.py`
- `switchover` dan `failover`: `oracle_auto/phase_builders/role.py`

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

### prepare-storage-rules

Menyiapkan storage rules sebelum GI/ASM bergantung pada device:

- Generate udev rule dari `DM_UUID`.
- Reload dan trigger udev.
- Validasi symlink `/dev/oracleasm/...`.
- Cek collision symlink.

Command:

```bash
python main.py prepare-storage-rules --config configs/my-deployment.json --allow-storage-changes
```

### configure-asm-storage

Menyiapkan ASM storage setelah GI tooling tersedia:

- Validasi symlink `/dev/oracleasm/...`.
- Label AFD dari symlink `/dev/oracleasm/...`.
- Create diskgroup `OCR`, `DATA`, `RECO`.
- Validasi ASM diskgroup.

Command:

```bash
python main.py configure-asm-storage --config configs/my-deployment.json --allow-storage-changes
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

## 14.1 Execution Plan

Generate plan untuk review DBA sebelum SSH execution:

```bash
python main.py generate-plan --config configs/my-deployment.json
```

Output:

```text
.oracle-auto/reports/<run_id>-plan.html
.oracle-auto/reports/<run_id>-plan.json
.oracle-auto/reports/<run_id>-runbook.sh
.oracle-auto/reports/<run_id>-phase-runbooks/<phase>.sh
```

Plan dan runbook juga menampilkan mapping storage `DM_UUID -> /dev/oracleasm/... -> AFD label` untuk review sebelum storage command sungguhan.

## 14.2 Step Logs

Setiap step menyimpan stdout/stderr lokal:

```text
.oracle-auto/logs/<run_id>/<phase>/<host>/<step>.log
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
- ASM DM_UUID, udev symlink, AFD label, dan diskgroup mapping.
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

- Cek `DM_UUID` benar.
- Cek `udevadm info --export-db | grep DM_UUID`.
- Cek file rule `/etc/udev/rules.d/99-oracleasm.rules`.
- Cek symlink `/dev/oracleasm/...`.
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

Collect diagnostics:

```bash
python main.py collect-diagnostics --config configs/my-deployment.json
```

Cleanup lab terbatas:

```bash
python main.py cleanup-lab --config configs/my-deployment.json --yes
```

Rollback framework terbatas:

```bash
python main.py rollback-framework --config configs/my-deployment.json --yes
```

Cleanup/rollback ini menghapus artifacts framework seperti udev rule, generated `/etc/hosts` block, generated chrony block, selected staged diagnostics/state, dan restore `/etc/resolv.conf` dari backup jika ada. Command ini tidak menghapus Oracle home, database, ASM label, atau diskgroup.

## 19. Production Readiness Checklist

Sebelum production:

- Config sudah direview DBA dan infra.
- SCAN DNS sudah siap. Public/private/VIP sudah benar di config untuk ditulis ke `/etc/hosts`.
- Disk ASM sudah valid.
- Installer dan patch ZIP sudah benar.
- Dry-run semua command sudah direview.
- Precheck remote bersih dari `FAIL`.
- Backup atau rollback plan tersedia.
- Switchover/failover hanya dijalankan dengan window dan approval.

## 20. Lab Test Matrix

Sebelum production, validasi minimal:

- `single-gi` tanpa standby.
- `single-gi` dengan standby.
- `rac` tanpa standby.
- `rac` dengan standby method `manual`.
- `rac` dengan standby method `broker`.

Untuk setiap matrix, simpan `generate-plan`, `precheck`, `validate-deployment`, `patch-inventory`, dan HTML report sebagai bukti review.
