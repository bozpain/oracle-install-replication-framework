# Oracle Install Replication Framework

Automation framework untuk instalasi Oracle Database 19c, Grid Infrastructure, RAC, dan konfigurasi Data Guard.

Phase 1 berisi fondasi:

- CLI automation.
- Config loader dan validasi schema.
- SSH executor.
- State/resume sederhana.
- Precheck framework.

Target baseline:

- Python `3.12`.
- Oracle Linux `8.10` pada VM/server target.
- Server target punya akses ke Oracle Linux yum/dnf repository.
- Installer base Oracle dan patch `19.30` sudah tersedia di `/u01/sources`.

## Quick Start

Validasi config:

```bash
python main.py validate-config --config configs/sample-single.json
```

Lihat command precheck tanpa eksekusi remote:

```bash
python main.py precheck --config configs/sample-single.json --dry-run
```

Jalankan precheck ke server target:

```bash
python main.py precheck --config configs/sample-single.json
```

Untuk file YAML, install optional dependency `PyYAML` terlebih dahulu. File JSON bisa langsung dipakai tanpa dependency tambahan.
