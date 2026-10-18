"""Where a pore limited to the membrane region starts and ends: on the membrane surfaces, at the pore axis.

OPM files of entries without a production reference (opm_only/) are used as the structure too, they
contain the protein in the membrane frame.
"""

from pathlib import Path

import numpy as np
import pytest

from mole_api.pores.membrane import read_structure
from mole_api.pores.opm import membrane_from_opm
from mole_api.pores.path import PathError, end_points

REFERENCE = Path(__file__).parent / "data"


def membrane(structure_path: Path, opm_path: Path):
    structure = read_structure(structure_path)
    return structure, membrane_from_opm(structure, opm_path.read_text())


def opm_thickness(opm_path: Path) -> float:
    """OPM states half of the thickness in a REMARK."""
    line = next(line for line in opm_path.read_text().splitlines() if "1/2 of bilayer thickness" in line)
    return 2 * float(line.split()[-1])


def test_flat_membrane():
    """1k4c: the pore spans the membrane, OPM's thickness."""
    opm = REFERENCE / "1k4c" / "opm_1k4c.pdb"
    structure, m = membrane(REFERENCE / "1k4c" / "data_molecule", opm)
    start, end = end_points(structure, m, True, [])
    assert np.linalg.norm(end - start) == pytest.approx(opm_thickness(opm), abs=0.1)
    assert m.to_membrane_frame(start)[2] < m.to_membrane_frame(end)[2]  # from the N side to the O side


def test_curved_membrane():
    """2vv5: the surfaces span 9 and 12 Å in height, around the axis they are flat and OPM's thickness apart."""
    opm = REFERENCE / "2vv5" / "opm_2vv5.pdb"
    structure, m = membrane(REFERENCE / "2vv5" / "data_molecule", opm)
    start, end = (m.to_membrane_frame(p)[2] for p in end_points(structure, m, True, []))
    assert (start, end) == (pytest.approx(-114.1, abs=0.1), pytest.approx(-85.5, abs=0.1))
    assert end - start == pytest.approx(opm_thickness(opm), abs=0.1)


@pytest.mark.parametrize("pdb_id, message", [
    ("2zw3", "spans two membranes"),  # a gap junction channel, through the membranes of two cells
    ("6jnf", "no surface near the pore axis"),
])
def test_membrane_region_refused(pdb_id, message):
    opm = REFERENCE / "opm_only" / f"opm_{pdb_id}.pdb"
    structure, m = membrane(opm, opm)
    with pytest.raises(PathError, match=message):
        end_points(structure, m, True, [])
    end_points(structure, m, False, [])  # without the membrane region the pore can be computed
