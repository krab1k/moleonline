"""Command line entry point, a replacement of Pores.exe: `pores <pores.json>`.

The MOLE API writes pores.json into the submit directory and runs this program with its path.
Errors are reported on stderr (the API shows them to the user) with a non-zero exit code.
"""

import json
import sys
from pathlib import Path

from pores.membrane import MembraneError, download_opm, membrane_from_opm, membrane_json, read_structure


class PoresError(Exception):
    pass


def run(parameters_path: Path) -> None:
    parameters = json.loads(parameters_path.read_text())
    computation_dir = Path(parameters["WorkingDirectory"]).parent  # WorkingDirectory is the submit directory

    pdb_id = parameters.get("PdbId")
    if not pdb_id:
        raise PoresError("Pores for uploaded structures are not available yet.")
    structure_path = Path(parameters["UserStructure"]) if parameters.get("UserStructure") else computation_dir / f"{pdb_id}.cif"

    opm = download_opm(pdb_id)
    if opm is None:
        raise PoresError(f"The membrane position of {pdb_id} is not available in OPM.")
    membrane = membrane_from_opm(read_structure(structure_path), opm)
    (computation_dir / "membrane.json").write_text(json.dumps(membrane_json(membrane), separators=(",", ":")))

    # never report a finished computation without a pore
    raise PoresError("The pore itself is not computed yet, only the membrane position.")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: pores <pores.json>", file=sys.stderr)
        sys.exit(2)
    try:
        run(Path(sys.argv[1]))
    except (PoresError, MembraneError) as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except OSError as e:  # network (OPM) and file errors
        print(f"The pore computation failed: {e}", file=sys.stderr)
        sys.exit(1)
