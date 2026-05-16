# Patch Manifests

`version.patch_set` and `installer.patch_manifest` select `manifests/<patch_set>.yaml`.

To add a new RU, copy `19.30.yaml` to the new patch set, for example `19.31.yaml`, then update:

- `patch_id`
- `opatch_zip`, `gi_zip`, `dbru_zip`, `ojvm_zip`
- `opatch_dir`, `gi_dir`, `dbru_dir`, `ojvm_dir`
- `pre_datapatch_sql` when the pre-datapatch SQL differs

Patch ZIP files must be placed in `/u01/sources` on every target. The framework unzips patch bundles directly into `/u01/sources` and points `-applyRU` to the manifest directory names.
