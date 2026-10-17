# Test data

Results of the original `Pores.exe` (Windows, MOLE API on api.mole.upol.cz), to validate the Linux replacement.
Each folder is one production computation, created with `Init/Pores/<pdb>` (the preferred biological assembly
from PDBe) or an upload, followed by one `Submit/Pores`. Only what the tests read is kept.

| Folder | Date | Versions | Submission | Notes |
|---|---|---|---|---|
| `1k4c` | 2026-10-14 | Pores 1.4.5, MOLE 2.5.18.1.14 | `InMembrane=true`, probe 13, threshold 0.8 | `submit2_inmembrane_false_data_json` is a second submission with `InMembrane=false` |
| `2vv5` | 2026-10-16 | Pores 1.4.5, MOLE 2.5.18.1.14 | `InMembrane=false`, probe 13, threshold 0.8 | OPM's membrane is curved; a submission with `InMembrane=true` found no path |
| `3ldc` | 2026-10-16 | Pores 1.4.5, MOLE 2.5.18.1.14 | `InMembrane=true`, probe 10, threshold 0.8 | |
| `3ldc_upload` | 2026-10-16 | Pores 1.4.5, MOLE 2.5.18.1.14 | as `3ldc` | `uploaded_3ldc.cif` (the `3ldc` structure) uploaded with `POST Init`, so Pores used MEMEMBED instead of OPM; the path is identical to `3ldc` |
| `opm_only` | 2026-10-17 | | | OPM files of entries without a production result, used as the structure too: 2ZW3 (two membranes), 5NG5 (dummy atoms after `END`), 6JNF (no membrane surface near the pore axis) |

Files:

- `data_molecule` – the structure the computation used (`Data?format=molecule`)
- `opm_<pdb>.pdb` – the OPM file of the entry
- `data_membrane` – `Data?format=membrane`, the `membrane.json` points (`Position`, `Side` N/O)
- `data_json` – `Data?format=json`, MOLE output trimmed to `Version`, the Pores parameters (`Config`) and the
  pore, `Channels.Paths[0]`
