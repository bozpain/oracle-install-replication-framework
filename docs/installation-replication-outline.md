# Oracle GI, ASM, Database, and Active Data Guard Blueprint

Dokumen ini adalah blueprint desain automation. README memberi overview singkat; dokumen ini menjelaskan scope, prinsip desain, keputusan default, dan roadmap kemampuan framework. Untuk instruksi paling detail per command, gunakan [Deployment Guide](deployment_guide.md).

## 1. Executive Summary

Framework ini dibuat untuk mempercepat deployment Oracle yang biasanya panjang, sensitif terhadap urutan, dan rawan variasi antar environment. Targetnya bukan hanya menjalankan installer, tetapi membentuk workflow end-to-end yang dapat diaudit:

1. Validasi config dan topology.
2. Precheck target host.
3. Persiapan OS fresh install.
4. Verifikasi installer dan patch yang sudah disalin manual.
5. Persiapan persistent device path dari `DM_UUID`, `/dev/disk/by-id/...`, atau `/dev/mapper/<alias>`.
6. Instalasi Grid Infrastructure.
7. Konfigurasi ASMLib v3 dan diskgroup.
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

- SCAN cukup berupa DNS name.
- SCAN tidak dimasukkan ke `/etc/hosts`.
- Automation mengatur DNS resolver target, lalu mengecek SCAN resolve melalui DNS.
- Jika SCAN tidak resolve, precheck gagal.
- Public, private, dan VIP tidak divalidasi lewat DNS. Ketiganya menjadi generated `/etc/hosts` entries supaya install tidak tergantung DNS selain SCAN.

## 5. OS Baseline

OS preparation berjalan otomatis pada semua node.

Automation mengelola:

- Install package baseline termasuk `oracle-database-preinstall-19c`, `chrony`, `unzip`, dan package pendukung.
- Group Oracle standar.
- User `grid` dan `oracle`.
- Direktori `/u01/app/grid`, Grid home, Oracle base, DB home, `/u01/sources`, dan `/u01/stage`.
- Ownership dan permission.
- DNS resolver di `/etc/resolv.conf`.
- `/etc/hosts` untuk public/private/VIP primary dan standby.
- SELinux menjadi `permissive`.
- Disable firewall service.
- Chrony ke NTP `192.168.113.41` dan `192.168.115.41`.

## 6. Storage Model

Storage selalu ASM. Untuk deployment produksi, user memberikan persistent path `/dev/disk/by-id/...`, stable multipath alias `/dev/mapper/<alias>`, atau disk `DM_UUID` secara manual di config, bukan `/dev/mapper/mpathX` atau `/dev/sdX`, karena nama device generik tersebut bisa berubah setelah reboot atau rediscovery. Jika memakai `DM_UUID`, framework menurunkannya menjadi `/dev/disk/by-id/dm-uuid-mpath-...`. Lab non-multipath boleh memakai object `path`, `site_paths`, atau `node_paths`; path itu harus sudah ada sebagai block device pada target host sebelum precheck dijalankan.

- `ocr_disks` untuk diskgroup `OCR`.
- `data_disks` untuk diskgroup `DATA`.
- `reco_disks` untuk diskgroup `RECO`.
- `redundancy`, default `EXTERNAL`.

Automation melakukan:

- Normalisasi input UUID menjadi `DM_UUID=mpath-<uuid>` jika prefix `mpath-` belum ada.
- Validasi duplicate IP public/private/VIP, duplicate generated hostname, duplicate SCAN, duplicate disk UUID, duplicate ASMLib label, dan minimum disk count sesuai redundancy.
- Resolve persistent device path dari `path`, `site_paths`, atau `node_paths` config, default `/dev/disk/by-id/dm-uuid-mpath-<uuid>` untuk input `DM_UUID`.
- Set owner `grid`, group `asmdba`, dan mode `0660` pada resolved block device.
- Validasi persistent path sudah menjadi block device.
- Label disk dengan ASMLib v3 memakai `oracleasm createdisk <LABEL> <resolved-path>`.
- Create diskgroup `OCR`, `DATA`, dan `RECO`.
- Validasi diskgroup terlihat pada target.
- Report mapping `DM_UUID`, persistent device path, ASMLib label `ORCL:<LABEL>`, dan diskgroup.
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
11. `update-opatch`
12. `analyze-patch`
13. `apply-grid-patch`
14. `apply-db-patch`
15. `datapatch`
16. `patch-inventory`
17. `apply-patch`
18. `create-database`
19. `setup-active-dataguard`
20. `setup-dataguard-broker`
21. `validate-deployment`
22. `switchover`
23. `failover`
24. `generate-plan`
25. `generate-report`
26. `collect-diagnostics`
27. `cleanup-lab`
28. `rollback-framework`

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
- ASM DM_UUID, persistent device path, ASMLib label, and diskgroup mapping.
- Installer and patch list.
- Execution result.
- Error and warning summary.
- Failed steps first, per-step log path, SCAN section, Data Guard section, dan storage mapping detail.

## 12. Validation Note

Framework sudah membangun automation skeleton yang serius: schema, runner, command phases, dry-run, state, report, runbook artifact, secret redaction, dan tests. Bagian yang menyentuh Oracle installer, GI response file, root scripts, ASMLib/ASM storage, OPatch/opatchauto, DBCA, RMAN duplicate, Broker, switchover, dan failover tetap harus divalidasi di lab target sebelum production.
