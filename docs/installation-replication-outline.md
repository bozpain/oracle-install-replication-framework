# Oracle GI, ASM, Database, and Active Data Guard Blueprint

Dokumen ini adalah blueprint desain automation. README memberi overview singkat; dokumen ini menjelaskan scope, prinsip desain, keputusan default, dan roadmap kemampuan framework. Untuk instruksi paling detail per command, gunakan [Deployment Guide](deployment_guide.md).

## 1. Executive Summary

Framework ini dibuat untuk mempercepat deployment Oracle yang biasanya panjang, sensitif terhadap urutan, dan rawan variasi antar environment. Targetnya bukan hanya menjalankan installer, tetapi membentuk workflow end-to-end yang dapat diaudit:

1. Validasi config dan topology.
2. Precheck target host.
3. Persiapan OS fresh install.
4. Verifikasi installer dan patch yang sudah disalin manual.
5. Persiapan ASM storage: semua source disk (`path`, by-id/by-uuid, `ID_SERIAL`, `ID_WWN`, atau `DM_UUID`) dibuatkan alias stabil `/dev/oracleasm/<LABEL>` lebih dulu.
6. Instalasi Grid Infrastructure.
7. Konfigurasi storage sesuai mode: `raw` memakai alias langsung, ASMLib v3 melabeli dari alias, AFD melabeli dari alias, lalu diskgroup dibuat.
8. Instalasi Oracle Database software.
9. Patching.
10. Pembuatan database primary.
11. Active Data Guard jika standby diisi.
12. Data Guard Broker jika dipilih.
13. Validasi deployment.
14. Switchover/failover automation.
15. HTML report, execution plan, dan diagnostics.

## 2. Deployment Scope

Install type hanya terdiri dari:

- `single-gi`: satu node dengan Grid Infrastructure, ASM, dan database.
- `rac`: RAC dengan Grid Infrastructure, ASM, dan database.

Keputusan scope:

- Tidak ada `single-db` tanpa GI.
- Tidak ada mode filesystem database.
- Semua deployment memakai ASM.
- Semua deployment memakai user terpisah: `grid` untuk GI/ASM dan `oracle` untuk database.
- Standby mengikuti bentuk primary: `single-gi` ke `single-gi`, `rac` ke `rac`.
- Jika `standby_site` ada, Active Data Guard aktif otomatis.

## 3. Baseline Platform

Baseline awal:

- OS: Oracle Linux `8.10`.
- GI/Database: Oracle `19c`.
- Patch set: `19.30`.
- Python: `3.12`.

Framework harus tetap expandable untuk:

- Oracle Linux `9`.
- Oracle Database `26ai`.
- Patch berikutnya seperti `19.31`.
- RU, OJVM, one-off patch, dan patch lain yang ditambahkan ke config.

## 4. Network Model

Input utama per node:

- Public hostname/FQDN.
- Public IP.
- Private interconnect IP untuk RAC.
- VIP IP untuk RAC.

Generated hostname:

- Private hostname otomatis dari public hostname dengan suffix `-priv`.
- VIP hostname otomatis dari public hostname dengan suffix `-vip`.
- Contoh: `db1.example.com` menjadi `db1-priv.example.com` dan `db1-vip.example.com`.

SCAN:

- SCAN cukup berupa DNS name jika DNS target bisa resolve.
- Jika config mengisi `scan_ip` atau `scan_ips`, SCAN menjadi generated `/etc/hosts` entry.
- Automation mengatur DNS resolver target, lalu mengecek DNS hanya untuk SCAN yang tidak punya `scan_ip`/`scan_ips`.
- Jika SCAN tanpa `scan_ip`/`scan_ips` tidak resolve dari DNS, precheck gagal.
- Public, private, VIP, dan host-managed SCAN tidak divalidasi lewat DNS. Semuanya menjadi generated `/etc/hosts` entries.

## 5. OS Baseline

OS preparation berjalan otomatis pada semua node.

Automation mengelola:

- Install package baseline termasuk `oracle-database-preinstall-19c`, `chrony`, `unzip`, dan package pendukung.
- Group Oracle standar.
- User `grid` dan `oracle`.
- Direktori `/u01/app/grid`, Grid home, Oracle base, DB home, `/u01/sources`, dan `/u01/stage`.
- Ownership dan permission.
- DNS resolver di `/etc/resolv.conf`.
- `/etc/hosts` untuk public/private/VIP primary dan standby, plus SCAN jika `scan_ip`/`scan_ips` diisi.
- SELinux menjadi `permissive`.
- Disable firewall service.
- Chrony ke NTP `192.168.113.41` dan `192.168.115.41`.

## 6. Storage Model

Storage selalu ASM. `prepare-storage-rules` membaca source disk dari config (`path`, by-id/by-uuid, `ID_SERIAL`, `ID_WWN`, atau `DM_UUID`) lalu menulis `/etc/udev/rules.d/99-oracle-asm.rules` untuk membuat symlink stabil `/dev/oracleasm/<LABEL>`. Setelah alias valid, mode `raw` memakai `/dev/oracleasm/<LABEL>` langsung di ASM diskstring dan create diskgroup, mode `asmlibv3` menjalankan `oracleasm createdisk <LABEL> /dev/oracleasm/<LABEL>`, dan mode `afd` menjalankan `asmcmd afd_label <LABEL> /dev/oracleasm/<LABEL>`.

- `ocr_disks` untuk diskgroup `OCR`.
- `data_disks` untuk diskgroup `DATA`.
- `reco_disks` untuk diskgroup `RECO`.
- `redundancy`, default `EXTERNAL`.

Automation melakukan:

- Normalisasi input UUID menjadi `DM_UUID=mpath-<uuid>` jika prefix `mpath-` belum ada.
- Validasi duplicate IP public/private/VIP, duplicate generated hostname, duplicate SCAN, duplicate disk UUID, duplicate ASM label, dan minimum disk count sesuai redundancy.
- Alias disk: tulis udev rule yang menghasilkan `SYMLINK+="oracleasm/<LABEL>"`, owner `grid`, group `asmdba`, mode `0660`, lalu `udevadm control --reload-rules` dan `udevadm trigger`.
- Source disk: resolve `DM_UUID`, `ID_SERIAL`, `ID_WWN`, atau `path` ke block device, lalu validasi alias `/dev/oracleasm/<LABEL>` menunjuk ke device tersebut.
- Label disk dengan ASMLib v3 memakai `oracleasm createdisk <LABEL> /dev/oracleasm/<LABEL>`.
- ASM diskstring: `raw` memakai `/dev/oracleasm/<LABEL>`, `asmlibv3` memakai `ORCL:*`, dan `afd` memakai `AFD:*`.
- Create diskgroup `OCR`, `DATA`, dan `RECO`.
- Validasi diskgroup terlihat pada target.
- Report mapping source disk, ASM label, alias path, dan diskgroup.
- Generate plan/runbook juga menampilkan storage mapping sebelum eksekusi supaya DBA bisa review disk yang akan disentuh.

## 7. Installer and Patch Model

Installer tidak di-upload oleh framework. Operator menyalin file ZIP manual ke target, default:

```text
/u01/sources
```

Config mendefinisikan:

- Grid Infrastructure base ZIP.
- Oracle Database base ZIP.
- OPatch ZIP jika diperlukan.
- Patch list berurutan.
- `patch_id` opsional per patch untuk validasi `opatch lspatches`; disarankan diisi eksplisit untuk Grid RU dan DB RU.

Automation melakukan:

- Cek path.
- Cek file ada dan size tidak `0`.
- Cek permission baca untuk user `grid` dan `oracle`.
- Deteksi patch top setelah unzip memakai Oracle patch inventory layout, dengan fallback ke top-level folder.
- Ekstrak installer ke home yang sesuai.
- Apply patch sesuai urutan.
- Simpan inventory patch ke report.

## 8. Data Guard Model

Active Data Guard aktif otomatis jika `standby_site` diisi.

Default:

- Protection mode: `max_performance`.
- Standby dibuat otomatis dari primary.
- RMAN duplicate baseline: `FROM ACTIVE DATABASE`.

Configuration method:

- `manual`: konfigurasi physical standby tanpa Broker.
- `broker`: aktifkan Data Guard Broker, create configuration, add standby, enable, dan validate.

## 9. Operations Model

Role operation masuk scope framework:

- `switchover`: planned role transition.
- `failover`: disaster role transition dengan guardrail `--yes`.

Prinsip:

- Switchover harus melakukan readiness validation.
- Failover harus eksplisit dan meninggalkan catatan bahwa former primary perlu rebuild atau reinstate.
- Semua operation masuk HTML report.

## 10. Command Roadmap

Status baseline: semua command roadmap sudah tersedia sebagai struktur Python, mendukung `--dry-run`, state/resume, output JSON, dan HTML report.

1. `validate-config`
2. `doctor`
3. `inventory`
4. `precheck`
5. `prepare-os`
6. `verify-installer`
7. `prepare-storage-rules`
8. `install-grid`
9. `configure-asm-storage`
10. `install-db-software`
11. `apply-ojvm-patch`
12. `create-database`
13. `patch-inventory`
14. `configure-dataguard`
16. `validate-deployment`
17. `update-opatch` (manual/advanced untuk home existing; fresh install sudah update OPatch di `install-grid` dan `install-db-software`)
18. `analyze-patch`
19. `apply-grid-patch`
20. `apply-db-patch`
21. `datapatch`
22. `apply-patch`
23. `switchover`
24. `failover`
25. `generate-plan`
26. `generate-report`
27. `collect-diagnostics`
28. `cleanup-lab`
29. `rollback-framework`

## 11. State and Reporting

State path:

```text
.oracle-auto/state/<run_id>.json
```

Report path:

```text
.oracle-auto/reports/<run_id>.html
```

Report berisi:

- Run identity.
- Version baseline.
- Topology.
- Generated hostname.
- DNS resolver dan SCAN validation.
- ASM source disk, `/dev/oracleasm/<LABEL>` alias, storage label, and diskgroup mapping.
- Installer and patch list.
- Execution result.
- Error and warning summary.
- Failed steps first, per-step log path, SCAN section, Data Guard section, dan storage mapping detail.

## 12. Validation Note

Framework sudah membangun automation skeleton yang serius: schema, runner, command phases, dry-run, state, report, runbook artifact, secret redaction, dan tests. Bagian yang menyentuh Oracle installer, GI response file, root scripts, ASMLib/ASM storage, OPatch/opatchauto, DBCA, RMAN duplicate, Broker, switchover, dan failover tetap harus divalidasi di lab target sebelum production.
