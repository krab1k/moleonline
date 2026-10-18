"""The membrane predicted by MEMEMBED against OPM and against the original Pores (see data/README.md).

Needs MEMEMBED: set MEMEMBED to its path, otherwise the tests are skipped. The path test also needs MOLE2.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from mole_api.pores.membrane import read_structure
from mole_api.pores.memembed import membrane_from_memembed
from mole_api.pores.opm import membrane_from_opm
from mole_api.pores.path import end_points, mole_input

REFERENCE = Path(__file__).parent / "data"
MEMEMBED = os.environ.get("MEMEMBED")
MOLE = os.environ.get("MOLE2")

pytestmark = pytest.mark.skipif(not MEMEMBED, reason="set MEMEMBED to the memembed executable")


@pytest.mark.parametrize("pdb_id", ["1k4c", "2vv5", "3ldc"])
def test_close_to_opm(pdb_id):
    structure = read_structure(REFERENCE / pdb_id / "data_molecule")
    opm = membrane_from_opm(structure, (REFERENCE / pdb_id / f"opm_{pdb_id}.pdb").read_text())
    predicted = membrane_from_memembed(structure, MEMEMBED, False, [])
    assert predicted.rmsd < 0.01

    normal, opm_normal = predicted.rotation[:, 2], opm.rotation[:, 2]
    assert normal @ opm_normal > np.cos(np.radians(10))  # also the same N and O sides
    thickness = lambda m: m.heights["O"].mean() - m.heights["N"].mean()
    assert abs(thickness(predicted) - thickness(opm)) < 5  # optimised by MEMEMBED (-q 1): 25.0-33.3 Å here
    # the pore axis passes through the same place
    on_axis = predicted.to_structure([0.0, 0.0, 0.0])
    offset = opm.to_membrane_frame(on_axis)
    assert np.hypot(offset[0], offset[1]) < 2


def test_repeatable():
    structure = read_structure(REFERENCE / "3ldc" / "data_molecule")
    first = membrane_from_memembed(structure, MEMEMBED, False, [])
    second = membrane_from_memembed(structure, MEMEMBED, False, [])
    assert np.allclose([p for p, _ in first.points], [p for p, _ in second.points])


@pytest.mark.skipif(not MOLE, reason="set MOLE2 to the mole2 executable")
def test_upload_same_path_as_production(tmp_path):
    """3LDC uploaded as a user structure: the original Pores used MEMEMBED and found the same pore as with OPM."""
    structure_path = tmp_path / "3ldc.cif"
    shutil.copy(REFERENCE / "3ldc_upload" / "uploaded_3ldc.cif", structure_path)
    structure = read_structure(structure_path)
    membrane = membrane_from_memembed(structure, MEMEMBED, False, [])
    working_directory = tmp_path / "1"
    working_directory.mkdir()
    start, end = end_points(structure, membrane, True, [])
    (working_directory / "input.xml").write_text(mole_input(structure_path, working_directory, start, end, 10.0, 0.8, []))
    subprocess.run([MOLE, str(working_directory / "input.xml")], check=True, capture_output=True)

    ours = json.loads((working_directory / "json" / "data.json").read_text())["Channels"]["Paths"][0]["Profile"]
    theirs = json.loads((REFERENCE / "3ldc_upload" / "data_json").read_text())["Channels"]["Paths"][0]["Profile"]
    assert round(ours[-1]["Distance"], 1) == round(theirs[-1]["Distance"], 1)
    assert round(min(n["Radius"] for n in ours), 3) == round(min(n["Radius"] for n in theirs), 3)
