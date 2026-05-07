# Garis Besar Instalasi Oracle GI, Database, dan Active Data Guard

Dokumen ini adalah draft runbook end-to-end untuk framework instalasi Oracle Grid Infrastructure, Oracle Database, ASM storage, patching, dan Active Data Guard. Tujuannya supaya setiap langkah bisa direview lebih awal: mana yang perlu di-adjust, ditunda, atau dihapus sebelum automation dibuat lebih jauh.

Status framework saat ini: **Phase 1 foundation**. Yang sudah tersedia di kode adalah validasi config, SSH executor, state/resume sederhana, dan precheck dasar. Langkah persiapan OS, storage ASM, installer verification, Grid, Database, patching, Active Data Guard, switchover/failover, dan report HTML di bawah ini adalah target automation berikutnya.

## 1. Scope Final

1. Tipe instalasi hanya ada 2:
   - `single-gi`: single node dengan Grid Infrastructure, ASM, dan database.
   - `rac`: RAC dengan Grid Infrastructure, ASM, dan database.
2. Semua deployment menggunakan ASM.
3. Semua deployment menggunakan Grid Infrastructure.
4. Jika standby diisi, automation langsung membuat Active Data Guard.
5. Active Data Guard default:
   - Protection mode: `max_performance`.
   - Standby dibuat otomatis dari input standby.
   - Tidak perlu input mode Data Guard dasar.
6. Topologi standby mengikuti primary:
   - Primary `single-gi` berarti standby juga single.
   - Primary `rac` berarti standby juga RAC.
7. Output akhir automation berupa report HTML.
8. Switchover dan failover masuk scope automation.

## 2. Baseline Versi

1. Baseline awal:
   - OS: Oracle Linux `8.10`.
   - Oracle Grid Infrastructure/Database: `19c`.
   - Patch awal: `19.30`.
2. Versi harus dibuat expandable:
   - Oracle Linux `9`.
   - Oracle Database `26ai`.
   - Patch berikutnya seperti `19.31` dan seterusnya.
3. Patch tidak dianggap fixed. Config harus mendukung daftar patch yang terus bertambah.

## 3. Input Utama dari User

1. Identitas run:
   - `run_id`
   - Nama environment/site.
2. Tipe instalasi:
   - `single-gi`
   - `rac`
3. Primary site:
   - Public hostname/FQDN.
   - Public IP.
   - Private interconnect IP.
   - Private hostname otomatis dari public hostname dengan suffix `-priv`.
   - VIP hostname otomatis dari public hostname dengan suffix `-vip` untuk RAC.
   - VIP IP untuk RAC.
   - SCAN DNS name untuk RAC.
   - DNS resolver yang akan dipakai target server.
   - Contoh naming otomatis: `db1.example.com` menjadi `db1-vip.example.com` dan `db1-priv.example.com`.
4. Standby site, optional:
   - Jika standby IP/host diisi, Active Data Guard otomatis aktif.
   - Input standby harus mengikuti bentuk primary: single ke single, RAC ke RAC.
5. Database identity:
   - `db_name`
   - Primary `db_unique_name`
   - Standby `db_unique_name` jika standby ada.
6. Disk ASM:
   - List disk untuk `OCR`.
   - List disk untuk `DATA`.
   - List disk untuk `RECO`.
   - Redundancy per diskgroup jika perlu.
7. Installer dan patch:
   - Path installer, default `/u01/sources`.
   - Nama file ZIP Grid Infrastructure base.
   - Nama file ZIP Oracle Database base.
   - Nama file patch RU/RUR/one-off.
   - Nama file OPatch jika perlu update OPatch.
8. Opsi Data Guard lanjutan:
   - `manual`: konfigurasi physical standby tanpa Broker.
   - `broker`: konfigurasi Data Guard Broker.

## 4. Persiapan Config Framework

1. Buat config deployment baru dari sample.
2. Isi `run_id` unik.
3. Isi `install_type` hanya dengan `single-gi` atau `rac`.
4. Isi blok `version`:
   - `os_version`
   - `oracle_version`
   - `patch_set`
5. Isi blok `primary_site`.
6. Isi blok `standby_site` hanya jika ingin Active Data Guard.
7. Isi seluruh detail IP untuk `/etc/hosts`:
   - Public hostname dan public IP.
   - Private IP; private hostname dibuat otomatis dari public hostname.
   - VIP IP; VIP hostname dibuat otomatis dari public hostname.
   - Standby public/private/VIP jika standby ada.
   - SCAN tidak ditulis ke `/etc/hosts`; SCAN wajib resolve dari DNS.
8. Isi blok DNS:
   - Resolver DNS target untuk `/etc/resolv.conf`.
   - SCAN DNS name.
   - Automation harus mengatur resolver lalu memvalidasi SCAN resolve.
9. Isi blok `asm`:
   - `ocr_disks`
   - `data_disks`
   - `reco_disks`
   - redundancy.
10. Isi blok `installer`:
   - `sources_path`
   - `grid_zip`
   - `db_zip`
   - `opatch_zip`
   - `patches`
11. Isi blok `dataguard` hanya untuk pilihan konfigurasi lanjutan:
   - `configuration_method`: `manual` atau `broker`.
   - Protection mode tetap default `max_performance`.

## 5. Validasi Config

1. Jalankan validasi config:

   ```bash
   python main.py validate-config --config configs/my-deployment.json
   ```

2. Validasi harus memastikan:
   - `install_type` hanya `single-gi` atau `rac`.
   - Semua deployment memakai ASM.
   - Semua deployment punya disk `OCR`, `DATA`, dan `RECO`.
   - Jika standby diisi, jumlah node standby sama dengan primary.
   - Jika primary single, standby juga single.
   - Jika primary RAC, standby juga RAC.
   - Detail IP untuk `/etc/hosts` lengkap.
   - Private hostname bisa diturunkan otomatis dari public hostname.
   - VIP hostname bisa diturunkan otomatis dari public hostname.
   - SCAN DNS name tidak punya IP di config dan harus resolve via DNS.
   - DNS resolver tersedia dan bisa dipakai target server.
   - Disk tidak duplikat antar diskgroup.
   - Installer ZIP yang disebut di config ada di `sources_path`.
   - Patch list valid dan berurutan.
   - Data Guard method hanya `manual` atau `broker`.

## 6. Dry Run

1. Jalankan dry run untuk melihat command yang akan dieksekusi:

   ```bash
   python main.py precheck --config configs/my-deployment.json --dry-run
   ```

2. Dry run untuk fase berikutnya juga harus tersedia:
   - OS preparation.
   - Storage ASM preparation.
   - Installer verification.
   - Grid installation.
   - Database software installation.
   - Patch apply.
   - Database creation.
   - Active Data Guard setup.
   - Switchover/failover.
   - HTML report generation.

## 7. Precheck Remote

1. Jalankan precheck ke server target:

   ```bash
   python main.py precheck --config configs/my-deployment.json
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
3. Target precheck berikutnya:
   - Validasi OS fresh install.
   - Validasi semua IP/hostname resolve.
   - Validasi DNS resolver target.
   - Validasi SCAN DNS resolve; jika tidak resolve, precheck gagal.
   - Validasi disk ASM terlihat di semua node.
   - Validasi disk size konsisten per diskgroup.
   - Validasi installer ZIP dan patch ZIP ada.
   - Validasi tidak ada Oracle home/Grid home lama yang bentrok.

## 8. Persiapan OS Otomatis

Automation harus menjalankan OS preparation otomatis pada semua node.

1. Install package preinstall:

   ```bash
   dnf install -y oracle-database-preinstall-19c
   ```

2. Buat group standar:
   - `oinstall`
   - `dba`
   - `oper`
   - `backupdba`
   - `dgdba`
   - `kmdba`
   - `racdba`
   - `asmadmin`
   - `asmdba`
   - `asmoper`
3. Buat user terpisah:
   - `grid` untuk Grid Infrastructure dan ASM.
   - `oracle` untuk Oracle Database.
4. Set password atau lock policy sesuai desain yang nanti ditentukan.
5. Buat direktori standar:
   - `/u01/app/grid`
   - `/u01/app/19.0.0/grid`
   - `/u01/app/oracle`
   - `/u01/app/oracle/product/19.0.0/dbhome_1`
   - `/u01/sources`
   - `/u01/stage`
6. Set ownership dan permission:
   - Grid home milik `grid:oinstall`.
   - DB home milik `oracle:oinstall`.
7. Set kernel parameters, limits, dan profile.
8. Set environment profile untuk `grid` dan `oracle`.
9. Set DNS resolver sesuai config.
10. Update `/etc/hosts` otomatis dari detail IP di config:
    - Public hostname.
    - Private hostname hasil suffix `-priv`.
    - VIP hostname hasil suffix `-vip`.
    - Primary dan standby jika standby ada.
    - SCAN tidak dimasukkan ke `/etc/hosts`.
11. Disable firewall otomatis:
    - `firewalld`
    - `iptables`
    - service firewall lain yang aktif jika ditemukan.
12. Set SELinux ke `permissive`.
13. Konfigurasi chrony otomatis ke NTP server:
    - `192.168.113.41`
    - `192.168.115.41`
14. Validasi chrony/time sync aktif setelah konfigurasi.
15. Jalankan reboot jika diperlukan dan lanjut via resume state.

## 9. Verifikasi Installer Otomatis

Installer tidak di-upload oleh automation. User copy manual file ZIP ke server target, lalu automation mengecek dan menggunakan file tersebut.

1. Cek `sources_path` ada dan readable.
2. Cek file Grid Infrastructure base ZIP ada.
3. Cek file Oracle Database base ZIP ada.
4. Cek file OPatch ZIP jika didefinisikan.
5. Cek semua patch ZIP yang didefinisikan.
6. Cek checksum jika checksum disediakan.
7. Cek ukuran file tidak `0`.
8. Cek user `grid` bisa membaca Grid ZIP dan patch yang relevan.
9. Cek user `oracle` bisa membaca DB ZIP dan patch yang relevan.
10. Ekstrak Grid ZIP ke Grid home.
11. Ekstrak DB ZIP ke DB home.
12. Simpan hasil verifikasi ke state dan report HTML.

## 10. Storage ASM Otomatis

Storage disiapkan oleh automation dari input disk manual untuk `OCR`, `DATA`, dan `RECO`.

1. Validasi semua disk dari config terlihat di OS.
2. Validasi disk belum dipakai filesystem atau diskgroup lain.
3. Validasi multipath jika digunakan.
4. Buat udev rules atau AFD label sesuai standar yang dipilih.
5. Add disk ke Oracle ASM Filter Driver.
6. Validasi label AFD:

   ```bash
   asmcmd afd_lslbl
   ```

7. Buat diskgroup `OCR`.
8. Buat diskgroup `DATA`.
9. Buat diskgroup `RECO`.
10. Set redundancy sesuai config.
11. Untuk RAC, validasi diskgroup terlihat konsisten di semua node.
12. Simpan mapping disk mentah ke label AFD dan diskgroup di report HTML.

## 11. Instalasi Grid Infrastructure

Langkah ini otomatis untuk `single-gi` dan `rac`.

1. Generate response file Grid Infrastructure.
2. Jalankan prerequisite check Grid.
3. Untuk RAC:
   - Validasi cluster node list.
   - Validasi SCAN DNS name resolve via DNS.
   - Validasi VIP hostname otomatis dan VIP IP per node.
   - Validasi private hostname otomatis dan private interconnect IP.
   - Validasi shared ASM disk `OCR`, `DATA`, dan `RECO`.
4. Jalankan silent install Grid Infrastructure.
5. Jalankan root script otomatis pada node yang diminta Oracle installer.
6. Jalankan konfigurasi ASM.
7. Mount diskgroup `OCR`, `DATA`, dan `RECO`.
8. Jalankan post-install check:
   - `crsctl check crs`
   - `crsctl stat res -t`
   - `asmcmd lsdg`

## 12. Instalasi Oracle Database Software dan Patching

1. Generate response file database software.
2. Jalankan prerequisite check database installer.
3. Jalankan silent install Oracle Database software.
4. Jalankan root script jika diminta.
5. Update OPatch jika `opatch_zip` didefinisikan.
6. Apply patch list sesuai urutan config.
7. Support patch yang bertambah:
   - RU utama.
   - One-off patch.
   - OJVM patch jika diperlukan.
   - Patch lain yang nanti ditambahkan ke config.
8. Jalankan post-patch inventory:

   ```bash
   opatch lsinventory
   ```

9. Simpan inventory dan patch status ke report HTML.

## 13. Pembuatan Database Primary

Automation langsung memakai metode terbaik untuk unattended install. Baseline: DBCA silent.

1. Generate DBCA response file.
2. Buat database primary di ASM `DATA`.
3. Set Fast Recovery Area di ASM `RECO`.
4. Untuk RAC, buat RAC database dan register ke Clusterware.
5. Set parameter utama:
   - `db_name`
   - `db_unique_name`
   - `control_files`
   - `db_create_file_dest`
   - `db_recovery_file_dest`
   - `db_recovery_file_dest_size`
   - `audit_file_dest`
6. Aktifkan archive log mode.
7. Aktifkan force logging:

   ```sql
   ALTER DATABASE FORCE LOGGING;
   ```

8. Buat standby redo logs otomatis jika standby ada.
9. Konfigurasi listener dan service.
10. Jalankan smoke test koneksi lokal dan remote.

## 14. Persiapan Standby Otomatis

Bagian ini berjalan otomatis jika standby host/IP diisi di config.

1. Jalankan OS preparation di standby.
2. Update resolver DNS dan `/etc/hosts` standby dengan semua IP primary dan standby, kecuali SCAN tetap DNS-only.
3. Jalankan storage ASM preparation di standby.
4. Install Grid Infrastructure di standby.
5. Install Oracle Database software di standby.
6. Apply patch level yang sama dengan primary.
7. Pastikan diskgroup `OCR`, `DATA`, dan `RECO` tersedia.
8. Konfigurasi listener standby.
9. Validasi konektivitas primary ke standby dan standby ke primary.

## 15. Konfigurasi Active Data Guard

Active Data Guard otomatis aktif jika standby diisi. Protection mode default selalu `max_performance`.

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
4. Jalankan RMAN duplicate standby dari active database:

   ```rman
   DUPLICATE TARGET DATABASE FOR STANDBY FROM ACTIVE DATABASE;
   ```

5. Start standby database.
6. Start managed recovery.
7. Buka standby read only dengan apply aktif untuk Active Data Guard jika lisensi/target mengizinkan.
8. Validasi apply log:
   - Archive log terkirim dari primary.
   - Managed recovery process aktif di standby.
   - Gap sequence tidak ada.
   - Apply lag sesuai threshold.
9. Jika `configuration_method=broker`, lanjutkan konfigurasi Data Guard Broker:
   - Enable broker.
   - Create broker configuration.
   - Add standby database.
   - Enable configuration.
   - Validate database.
10. Jika `configuration_method=manual`, simpan konfigurasi manual tanpa broker.

## 16. Validasi Setelah Instalasi

1. Validasi Grid/Clusterware:
   - `crsctl check crs`
   - `crsctl stat res -t`
2. Validasi ASM:
   - `asmcmd lsdg`
   - Diskgroup `OCR`, `DATA`, dan `RECO` mounted.
3. Validasi database:
   - `srvctl status database`
   - Listener aktif.
   - Alert log tidak ada error kritikal.
4. Validasi role database:

   ```sql
   SELECT name, open_mode, database_role FROM v$database;
   ```

5. Untuk Active Data Guard:
   - Cek transport lag.
   - Cek apply lag.
   - Cek archive gap.
   - Cek MRP aktif.
   - Cek standby open read only with apply jika Active Data Guard diaktifkan.

## 17. Switchover dan Failover Automation

Switchover dan failover masuk scope automation.

1. Command switchover harus menjalankan readiness check.
2. Switchover harus mendukung:
   - Manual Data Guard.
   - Data Guard Broker.
3. Setelah switchover:
   - Validasi role berubah.
   - Validasi service database.
   - Validasi transport/apply dari arah baru.
4. Command switchback harus tersedia.
5. Failover harus punya guardrail:
   - Konfirmasi eksplisit.
   - Report impact.
   - Catat database lama sebagai former primary.
6. Setelah failover:
   - Validasi primary baru.
   - Validasi service aktif.
   - Generate instruksi rebuild/reinstate former primary.

## 18. Report HTML

Output akhir automation berupa HTML report.

1. Report berisi:
   - Run ID.
   - Tanggal eksekusi.
   - Versi OS, GI, DB, OPatch, dan patch.
   - Topologi primary dan standby.
   - Mapping hostname/IP, generated private/VIP hostname, DNS resolver, SCAN DNS check, dan `/etc/hosts`.
   - Mapping disk ke AFD label dan diskgroup.
   - Status OS preparation.
   - Status installer verification.
   - Status Grid installation.
   - Status DB software installation.
   - Status patching.
   - Status database creation.
   - Status Active Data Guard.
   - Status switchover/failover jika dijalankan.
   - Error/warning summary.
2. Report harus tetap dibuat walaupun ada step gagal.
3. Report disimpan di folder output, contoh:

   ```text
   .oracle-auto/reports/<run_id>.html
   ```

## 19. State dan Resume

1. Framework menyimpan state default di:

   ```text
   .oracle-auto/state/<run_id>.json
   ```

2. Semua step automation harus resumable:
   - Precheck.
   - OS preparation.
   - Installer verification.
   - Storage ASM preparation.
   - Grid installation.
   - Database software installation.
   - Patch apply.
   - Database creation.
   - Standby preparation.
   - Active Data Guard setup.
   - Broker setup jika dipilih.
   - Validation.
   - Switchover/failover.
   - Report generation.
3. Gunakan `--no-resume` untuk memaksa step tertentu jalan ulang.

## 20. Roadmap Implementasi Framework

1. Update config schema untuk `single-gi` dan `rac`.
2. Tambah struktur config untuk IP, generated private/VIP hostname, DNS resolver, SCAN DNS name, `/etc/hosts`, ASM disk, installer, patch list, dan Data Guard method.
3. Tambah command `prepare-os`.
4. Tambah command `verify-installer`.
5. Tambah command `prepare-storage`.
6. Tambah command `install-grid`.
7. Tambah command `install-db-software`.
8. Tambah command `apply-patch`.
9. Tambah command `create-database`.
10. Tambah command `setup-active-dataguard`.
11. Tambah command `setup-dataguard-broker`.
12. Tambah command `validate-deployment`.
13. Tambah command `switchover`.
14. Tambah command `failover`.
15. Tambah command `generate-report`.
16. Tambah mode `--dry-run` untuk semua command.
17. Tambah HTML report untuk semua hasil eksekusi.
