"""Command line entry point, replacing Pores.exe: `pores <pores.json>`, or `pores --version`.

The MOLE API writes pores.json into the submit directory and runs this program with its path:

    Structure          the structure file of the computation
    PdbId              the PDB entry to look up in OPM, null when the structure is not the entry as
                       OPM has it (uploaded files, other assemblies)
    WorkingDirectory   the submit directory: input.xml is written there and MOLE writes its results there
    Membrane           where to write the membrane (membrane.json)
    InMembrane, IsBetaBarrel, Chains, ProbeRadius, InteriorThreshold
                       what the user submitted, chains as a list of names

The membrane comes from OPM for PDB entries it has, otherwise from MEMEMBED. The API then runs MOLE
with input.xml, MOLE is never run from here.
Settings of the installation are environment variables: MEMEMBED (the memembed executable) and
PORES_OPM_CACHE (directory for downloaded OPM files, no cache when unset).
Errors are reported on stderr (the API shows them to the user) with a non-zero exit code.
"""

import argparse
import importlib.metadata
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
    structure_path = Path(parameters["Structure"])
    working_directory = Path(parameters["WorkingDirectory"])
    chains = parameters["Chains"]

    structure = read_structure(structure_path)
    available = list(dict.fromkeys(chain.name for chain in structure[0]))
    unknown = [name for name in chains if name not in available]
    if unknown:
        raise PoresError(f"Chain{'s' if len(unknown) > 1 else ''} {', '.join(unknown)} {'are' if len(unknown) > 1 else 'is'} not in the structure. "
                         f"Available chains: {', '.join(available)}.")

    pdb_id = parameters.get("PdbId")
    cache = os.environ.get("PORES_OPM_CACHE")
    opm = download_opm(pdb_id, Path(cache) if cache else None) if pdb_id else None
    if opm is not None:
        membrane = membrane_from_opm(structure, opm)
    else:
        memembed = os.environ.get("MEMEMBED")
        if not memembed:
            raise PoresError("MEMEMBED is not configured, the membrane position cannot be predicted.")
        membrane = membrane_from_memembed(structure, memembed, parameters["IsBetaBarrel"], chains)
    Path(parameters["Membrane"]).write_text(json.dumps(membrane_json(membrane), separators=(",", ":")))

    start, end = end_points(structure, membrane, parameters["InMembrane"], chains)
    (working_directory / "input.xml").write_text(mole_input(structure_path, working_directory, start, end,
                                                          parameters["ProbeRadius"], parameters["InteriorThreshold"], chains))


def main() -> None:
    parser = argparse.ArgumentParser(prog="pores", description="Pore computation for MOLEonline, run by the MOLE API.")
    parser.add_argument("parameters", type=Path, help="pores.json written by the MOLE API")
    parser.add_argument("--version", action="version", version=f"%(prog)s {importlib.metadata.version('pores')}",
                        help="show the version and exit (the MOLE API reads it when it starts)")
    arguments = parser.parse_args()
    try:
        run(arguments.parameters)
    except (PoresError, MembraneError, PathError) as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except OSError as e:  # network (OPM), file errors, MEMEMBED not found
        print(f"The pore computation failed: {e}", file=sys.stderr)
        sys.exit(1)
