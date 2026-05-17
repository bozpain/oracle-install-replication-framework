# Patch Manifests

`patch_set` selects `manifests/<patch_set>.yaml`. The older
`version.patch_set` and `installer.patch_manifest` forms are still accepted,
but new operator configs should prefer the top-level `patch_set` key.

To add a new RU, copy `19.30.yaml` to the new patch set, for example `19.31.yaml`, then update:

- `patch_id`
- `grid_zip`, `db_zip`
- `opatch_zip`, `gi_zip`, `dbru_zip`, `ojvm_zip`
- `opatch_dir`, `gi_dir`, `dbru_dir`, `ojvm_dir`
- `oracleasmlib_rpm_x86_64` when the ASMLIB RPM filename changes
- `pre_datapatch_sql` when the pre-datapatch SQL differs

Base installer ZIPs, patch ZIPs, and ASMLIB RPMs must be placed in `/u01/sources` on every target. The framework unzips base homes into the Oracle homes, unzips patch bundles directly into `/u01/sources`, and points `-applyRU` to the manifest directory names.
