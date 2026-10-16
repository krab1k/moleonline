"""Pore paths computed with MOLE against production results of Pores.exe (see reference/README.md).

Needs the MOLE 2.5 command line tool: set MOLE2 to its path, otherwise the tests are skipped.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pores.membrane import membrane_from_opm, read_structure
from pores.path import PathError, end_points, mole_input

REFERENCE = Path(__file__).parent.parent / "reference"
MOLE = os.environ.get("MOLE2")

pytestmark = pytest.mark.skipif(not MOLE, reason="set MOLE2 to the mole2 executable")


def run_mole(input_path: Path, working_directory: Path) -> dict:
    """What the MOLE API does with input.xml, to compare the path with production."""
    subprocess.run([MOLE, str(input_path)], check=True, capture_output=True)
    return json.loads((working_directory / "json" / "data.json").read_text())


def compute(pdb_id: str, in_membrane: bool, probe_radius: float, tmp_path: Path) -> dict:
    structure_path = tmp_path / f"{pdb_id}.cif"
    shutil.copy(REFERENCE / pdb_id / "data_molecule", structure_path)
    structure = read_structure(structure_path)
    membrane = membrane_from_opm(structure, (REFERENCE / pdb_id / f"opm_{pdb_id}.pdb").read_text())
    working_directory = tmp_path / "1"
    working_directory.mkdir()
    start, end = end_points(structure, membrane, in_membrane, [])
    (working_directory / "input.xml").write_text(mole_input(structure_path, working_directory, start, end, probe_radius, 0.8, []))
    return run_mole(working_directory / "input.xml", working_directory)["Channels"]["Paths"][0]


def summary(path: dict) -> tuple:
    profile = path["Profile"]
    return round(profile[-1]["Distance"], 1), round(min(n["Radius"] for n in profile), 3), set(path["Layers"]["ResidueFlow"])


@pytest.mark.parametrize("pdb_id, reference, in_membrane, probe_radius", [
    ("1k4c", "data_json", True, 13.0),
    ("3ldc", "data_json", True, 10.0),
    ("2vv5", "data_json", False, 13.0),
])
def test_same_path(pdb_id, reference, in_membrane, probe_radius, tmp_path):
    length, bottleneck, residues = summary(compute(pdb_id, in_membrane, probe_radius, tmp_path))
    production = json.loads((REFERENCE / pdb_id / reference).read_text())["Channels"]["Paths"][0]
    p_length, p_bottleneck, p_residues = summary(production)
    assert (length, bottleneck) == (p_length, p_bottleneck)
    assert residues <= p_residues and len(residues) >= 0.95 * len(p_residues)


def test_1k4c_outside_membrane_close(tmp_path):
    """The old MOLE (2.5.18) ended this path at a different cavity point of the Fab above the channel."""
    length, bottleneck, residues = summary(compute("1k4c", False, 13.0, tmp_path))
    production = json.loads((REFERENCE / "1k4c" / "submit2_inmembrane_false_data_json").read_text())["Channels"]["Paths"][0]
    p_length, p_bottleneck, p_residues = summary(production)
    assert abs(length - p_length) < 5 and abs(bottleneck - p_bottleneck) < 0.1
    assert len(residues & p_residues) >= 0.9 * len(p_residues)


def test_curved_membrane_in_membrane_region_reported(tmp_path):
    """Production finished 2VV5 with InMembrane=true without any path; we say why instead."""
    with pytest.raises(PathError, match="curved"):
        compute("2vv5", True, 13.0, tmp_path)
