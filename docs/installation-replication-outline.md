# Garis Besar Instalasi Oracle dan Replikasi Data Guard

Dokumen ini adalah draft runbook end-to-end untuk framework instalasi Oracle Database 19c, Grid Infrastructure, RAC, dan Data Guard. Tujuannya supaya setiap langkah bisa direview lebih awal: mana yang perlu dipakai, di-adjust, ditunda, atau dihapus.

Status framework saat ini: **Phase 1 foundation**. Yang sudah tersedia di kode adalah validasi config, SSH executor, state/resume sederhana, dan precheck dasar. Langkah instalasi OS, Grid, Database, patching, dan Data Guard di bawah ini adalah outline target automation berikutnya.

## 1. Scope dan Keputusan Awal

1. Tentukan tipe instalasi:
   - `single-db`: 1 node database tanpa Grid/RAC.
   - `single-gi`: 1 node dengan Grid Infrastructure.
   - `rac-gi`: RAC/Grid only.
   - `rac-db`: RAC + database.
2. Tentukan apakah replikasi Data Guard aktif:
   - `replication.enabled=false` untuk primary only.
   - `replication.enabled=true` untuk primary + standby.
3. Tentukan mode Data Guard:
   - Baseline saat ini: `dataguard`.
   - Protection mode default: `max_performance`.
4. Tentukan versi baseline:
   - OS: Oracle Linux `8.10`.
   - Oracle Database/Grid: `19c`.
   - Patch target: `19.30`.
5. Tentukan model akses:
   - SSH user default: `root`.
   - Optional SSH key file.
   - Strict host key checking: default `accept-new`.

## 2. Persiapan Inventory

1. Kumpulkan daftar server primary.
2. Kumpulkan daftar server standby jika Data Guard aktif.
3. Untuk RAC, pastikan minimal 2 node di setiap site.
4. Siapkan informasi hostname:
   - Public hostname/FQDN setiap node.
   - SCAN name untuk RAC.
   - VIP name untuk setiap RAC node.
   - Private interconnect IP untuk setiap RAC node.
5. Siapkan informasi database:
   - `db_name`, contoh: `ORCL`.
   - Primary `db_unique_name`, contoh: `ORCL_A`.
   - Standby `db_unique_name`, contoh: `ORCL_B`.
6. Siapkan path installer di target:
   - Default: `/u01/sources`.
   - Harus readable oleh automation.

## 3. Persiapan Config Framework

1. Copy sample config sesuai skenario:
   - Single DB: `configs/sample-single.json`.
   - RAC + Data Guard: `configs/sample-rac-dg.json`.
2. Ubah `run_id` agar unik per eksekusi.
3. Ubah `install_type` sesuai target.
4. Ubah `sources_path` jika installer tidak berada di `/u01/sources`.
5. Ubah `patch_version` jika target bukan `19.30`.
6. Ubah blok `os` jika package manager memakai `yum` bukan `dnf`.
7. Ubah blok `ssh`:
   - `user`
   - `port`
   - `key_file` jika diperlukan
   - `connect_timeout`
8. Ubah `primary_site`:
   - `name`
   - `nodes`
   - `db_name`
   - `db_unique_name`
   - `scan_name`, `vip_names`, dan `private_interconnects` untuk RAC.
9. Ubah `standby_site` jika replikasi aktif.
10. Ubah blok `replication`:
   - `enabled`
   - `mode`
   - `protection_mode`

## 4. Validasi Config

1. Jalankan validasi config:

   ```bash
   python main.py validate-config --config configs/sample-rac-dg.json
   ```

2. Pastikan output menampilkan:
   - Install type sesuai target.
   - Jumlah primary node benar.
   - Standby site muncul jika replikasi aktif.
   - Replication disabled jika primary only.
3. Perbaiki config jika muncul error:
   - `standby_site` wajib saat `replication.enabled=true`.
   - RAC butuh minimal 2 node primary.
   - RAC + standby butuh minimal 2 node standby.
   - `sources_path` harus absolute path.
   - Hostname tidak boleh duplikat.

## 5. Dry Run Precheck

1. Jalankan dry run untuk melihat command yang akan dieksekusi:

   ```bash
   python main.py precheck --config configs/sample-rac-dg.json --dry-run
   ```

2. Review command per host.
3. Pastikan semua target host benar.
4. Pastikan SSH user benar.
5. Pastikan tidak ada command yang perlu dihilangkan untuk environment tertentu.

## 6. Precheck Remote

1. Jalankan precheck ke server target:

   ```bash
   python main.py precheck --config configs/sample-rac-dg.json
   ```

2. Framework saat ini mengecek:
   - Koneksi SSH.
   - OS release Oracle Linux `8.10`.
   - Kernel version.
   - Package manager `dnf` atau `yum`.
   - Enabled yum/dnf repositories.
   - Ketersediaan package `oracle-database-preinstall-19c`.
   - Path installer `/u01/sources`.
   - Kapasitas mount `/u01`.
   - User `oracle` sebagai warning.
   - User `grid` sebagai warning.
   - Time sync via `chronyc` atau `timedatectl`.
   - SELinux status.
   - FQDN hostname untuk RAC.
   - Network interface hint untuk RAC private interconnect.
3. Review status:
   - `PASS`: aman.
   - `WARN`: perlu review, tapi tidak langsung menggagalkan run.
   - `FAIL`: harus diperbaiki sebelum lanjut.
4. Jika ingin output JSON:

   ```bash
   python main.py precheck --config configs/sample-rac-dg.json --json
   ```

5. Jika ingin mengulang semua check dan mengabaikan state:

   ```bash
   python main.py precheck --config configs/sample-rac-dg.json --no-resume
   ```

## 7. Persiapan OS Target

Langkah ini belum otomatis di framework saat ini.

1. Install package preinstall:

   ```bash
   dnf install -y oracle-database-preinstall-19c
   ```

2. Pastikan user dan group tersedia:
   - `oracle`
   - `grid` untuk Grid/RAC.
   - `oinstall`, `dba`, `asmadmin`, `asmdba`, `asmoper` sesuai kebutuhan.
3. Buat direktori standar:
   - `/u01/app/oracle`
   - `/u01/app/grid`
   - `/u01/app/19.0.0/grid`
   - `/u01/app/oracle/product/19.0.0/dbhome_1`
   - `/u01/sources`
4. Set ownership dan permission.
5. Validasi kernel parameters dan limits.
6. Validasi swap, hugepages, dan shared memory.
7. Validasi hostname, DNS, dan `/etc/hosts`.
8. Validasi NTP/chrony aktif dan sinkron.
9. Validasi firewall rules untuk listener, SCAN, VIP, interconnect, dan Data Guard.
10. Validasi storage:
    - Filesystem untuk single instance.
    - ASM disk untuk GI/RAC.
    - Multipath jika digunakan.

## 8. Upload dan Verifikasi Installer

Langkah ini belum otomatis di framework saat ini.

1. Pastikan file installer tersedia di `sources_path`.
2. Verifikasi file Oracle Database 19c.
3. Verifikasi file Grid Infrastructure 19c jika diperlukan.
4. Verifikasi OPatch dan patch bundle target `19.30`.
5. Cocokkan checksum installer dan patch.
6. Ekstrak installer ke Oracle home atau Grid home sesuai metode instalasi silent.

## 9. Instalasi Grid Infrastructure

Langkah ini belum otomatis di framework saat ini.

Gunakan bagian ini hanya untuk `single-gi`, `rac-gi`, atau `rac-db`.

1. Generate response file Grid Infrastructure.
2. Jalankan prerequisite check Grid.
3. Untuk RAC:
   - Validasi cluster node list.
   - Validasi SCAN name.
   - Validasi VIP per node.
   - Validasi private interconnect.
   - Validasi shared ASM disks.
4. Jalankan silent install Grid Infrastructure.
5. Jalankan root script pada node yang diminta Oracle installer.
6. Jalankan konfigurasi ASM.
7. Buat ASM disk group:
   - `DATA`
   - `RECO`
   - Disk group lain sesuai desain.
8. Jalankan post-install check:
   - `crsctl check crs`
   - `crsctl stat res -t`
   - `asmcmd lsdg`

## 10. Instalasi Oracle Database Software

Langkah ini belum otomatis di framework saat ini.

1. Generate response file database software.
2. Jalankan prerequisite check database installer.
3. Jalankan silent install Oracle Database software.
4. Jalankan root script jika diminta.
5. Update OPatch jika diperlukan.
6. Apply patch bundle target `19.30`.
7. Jalankan post-patch inventory:

   ```bash
   opatch lsinventory
   ```

## 11. Pembuatan Database Primary

Langkah ini belum otomatis di framework saat ini.

1. Tentukan metode create database:
   - DBCA silent.
   - Manual script SQL.
2. Untuk RAC, pastikan database dibuat sebagai RAC database.
3. Set parameter utama:
   - `db_name`
   - `db_unique_name`
   - `control_files`
   - `db_recovery_file_dest`
   - `db_recovery_file_dest_size`
   - `audit_file_dest`
4. Aktifkan archive log mode.
5. Aktifkan force logging:

   ```sql
   ALTER DATABASE FORCE LOGGING;
   ```

6. Buat standby redo logs.
7. Konfigurasi listener dan service.
8. Jalankan smoke test koneksi lokal dan remote.

## 12. Persiapan Standby Site

Langkah ini belum otomatis di framework saat ini.

Gunakan bagian ini hanya jika `replication.enabled=true`.

1. Pastikan OS standby sudah melewati precheck.
2. Install Grid Infrastructure di standby jika target RAC/ASM.
3. Install Oracle Database software di standby.
4. Apply patch level yang sama dengan primary.
5. Pastikan struktur direktori/ASM disk group standby sesuai dengan primary.
6. Konfigurasi listener standby.
7. Pastikan konektivitas primary ke standby dan standby ke primary.

## 13. Konfigurasi Data Guard

Langkah ini belum otomatis di framework saat ini.

1. Set parameter Data Guard di primary:
   - `db_unique_name`
   - `log_archive_config`
   - `log_archive_dest_1`
   - `log_archive_dest_2`
   - `fal_server`
   - `fal_client`
   - `standby_file_management=AUTO`
2. Siapkan password file agar primary dan standby konsisten.
3. Siapkan `tnsnames.ora` untuk primary dan standby.
4. Buat standby control file atau gunakan RMAN duplicate.
5. Jalankan duplicate standby database:

   ```rman
   DUPLICATE TARGET DATABASE FOR STANDBY FROM ACTIVE DATABASE;
   ```

6. Start standby database dalam managed recovery mode.
7. Validasi apply log:
   - Archive log terkirim dari primary.
   - Managed recovery process aktif di standby.
   - Gap sequence tidak ada.
8. Set Data Guard protection mode sesuai config:
   - `max_performance`
   - Kandidat future: `max_availability`, `max_protection`.

## 14. Validasi Setelah Instalasi

1. Validasi service database.
2. Validasi listener.
3. Validasi alert log tidak ada error kritikal.
4. Untuk RAC:
   - `srvctl status database`
   - `srvctl status service`
   - `crsctl stat res -t`
5. Untuk Data Guard:
   - Cek transport lag.
   - Cek apply lag.
   - Cek archive gap.
   - Cek role primary/standby.
6. Jalankan query smoke test:

   ```sql
   SELECT name, open_mode, database_role FROM v$database;
   ```

## 15. Switchover dan Failover Test

Langkah ini perlu approval eksplisit sebelum dijalankan otomatis karena berisiko mengubah role database.

1. Jalankan readiness check switchover.
2. Jalankan switchover primary ke standby.
3. Validasi role berubah dengan benar.
4. Jalankan aplikasi smoke test.
5. Jalankan switchback jika diperlukan.
6. Dokumentasikan RTO/RPO aktual.
7. Failover test hanya dilakukan jika environment memang disposable atau sudah disetujui.

## 16. State, Resume, dan Audit

1. Framework menyimpan state default di:

   ```text
   .oracle-auto/state/<run_id>.json
   ```

2. Gunakan `run_id` unik untuk setiap deployment.
3. Gunakan `--no-resume` jika langkah precheck perlu dipaksa ulang.
4. Untuk fase berikutnya, setiap step instalasi sebaiknya juga dicatat ke state:
   - OS preparation.
   - Grid installation.
   - Database software installation.
   - Patch apply.
   - Database creation.
   - Data Guard setup.
   - Validation.

## 17. Bagian yang Perlu Di-adjust

Review dan isi bagian ini sebelum automation dibuat lebih jauh.

1. Apakah target utama single instance atau RAC?
2. Apakah standby juga RAC atau single standby?
3. Apakah pakai ASM atau filesystem?
4. Apakah storage sudah disiapkan manual atau mau diautomasi?
5. Apakah DNS sudah lengkap untuk public hostname, VIP, dan SCAN?
6. Apakah firewall dikelola oleh tim OS/network atau automation?
7. Apakah installer dan patch akan di-upload oleh automation atau sudah tersedia di `/u01/sources`?
8. Apakah user `oracle` dan `grid` dibuat oleh automation atau sudah distandarkan dari image VM?
9. Apakah patch `19.30` final, atau perlu dibuat configurable per environment?
10. Apakah Data Guard cukup physical standby manual, atau nanti perlu Data Guard Broker?
11. Apakah switchover/failover test masuk scope automation atau hanya dokumentasi manual?
12. Apakah output akhir perlu berupa report Markdown/JSON/HTML?

## 18. Roadmap Implementasi Framework

1. Tambah command `prepare-os`.
2. Tambah command `install-grid`.
3. Tambah command `install-db-software`.
4. Tambah command `apply-patch`.
5. Tambah command `create-database`.
6. Tambah command `setup-standby`.
7. Tambah command `configure-dataguard`.
8. Tambah command `validate-deployment`.
9. Tambah mode `--dry-run` untuk semua command.
10. Tambah state/resume untuk semua command.
11. Tambah report hasil eksekusi.

