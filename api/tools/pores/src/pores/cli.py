"""Command line entry point, replacing Pores.exe: `pores <pores.json>`.

The MOLE API writes pores.json into the submit directory and runs this program with its path.
The membrane comes from OPM for PDB entries it has, otherwise from MEMEMBED. It writes membrane.json
into the computation directory and the MOLE input (input.xml) into the submit directory; the API then
runs MOLE with it, MOLE is never run from here.
Errors are reported on stderr (the API shows them to the user) with a non-zero exit code.
"""

import json
import os
import sys
from pathlib import Path

from pores.membrane import MembraneError, membrane_json, read_structure
from pores.memembed import membrane_from_memembed
from pores.opm import download_opm, membrane_from_opm
from pores.path import PathError, end_points, mole_input


class PoresError(Exception):
    pass


def run(parameters_path: Path) -> None:
    parameters = json.loads(parameters_path.read_text())
    working_directory = Path(parameters["WorkingDirectory"])  # the submit directory
    computation_dir = working_directory.parent
    chains = list(parameters.get("Chains") or [])

    pdb_id = parameters.get("PdbId")  # set when the structure is the PDB entry as OPM has it
    structure_path = Path(parameters["UserStructure"]) if parameters.get("UserStructure") else computation_dir / f"{pdb_id}.cif"
    structure = read_structure(structure_path)

    cache = os.environ.get("PORES_OPM_CACHE")  # directory for downloaded OPM files, no cache when unset
    opm = download_opm(pdb_id, Path(cache) if cache else None) if pdb_id else None
    if opm is not None:
        membrane = membrane_from_opm(structure, opm)
    else:
        memembed = parameters.get("MemEmbedLocation")
        if not memembed:
            raise PoresError("MEMEMBED is not configured, the membrane position cannot be predicted.")
        membrane = membrane_from_memembed(structure, memembed, bool(parameters.get("IsBetaBarel")), chains)
    (computation_dir / "membrane.json").write_text(json.dumps(membrane_json(membrane), separators=(",", ":")))

    start, end = end_points(structure, membrane, bool(parameters.get("InMembrane")), chains)
    (working_directory / "input.xml").write_text(mole_input(structure_path, working_directory, start, end,
                                                          parameters.get("ProbeRadius") or 13.0, parameters.get("InteriorThreshold") or 0.8, chains))


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: pores <pores.json>", file=sys.stderr)
        sys.exit(2)
    try:
        run(Path(sys.argv[1]))
    except (PoresError, MembraneError, PathError) as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except OSError as e:  # network (OPM), file errors, MEMEMBED not found
        print(f"The pore computation failed: {e}", file=sys.stderr)
        sys.exit(1)
