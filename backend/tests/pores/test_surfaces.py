"""Where a pore limited to the membrane region starts and ends: on the membrane surfaces, at the pore axis.

OPM files of entries without a production reference (opm_only/) are used as the structure too, they
contain the protein in the membrane frame.
"""

from pathlib import Path

import gemmi
import numpy as np
import pytest

from moleonline.worker.pores.membrane import read_structure
from moleonline.worker.pores.opm import membrane_from_opm
from moleonline.worker.pores.path import PathError, end_points

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


def test_axis_of_chosen_chains_from_their_alpha_carbons():
    """One C-alpha per residue: a calcium ion (also named CA) or another conformation do not move the axis."""
    structure, m = membrane(REFERENCE / "1k4c" / "data_molecule", REFERENCE / "1k4c" / "opm_1k4c.pdb")
    chains = ["C", "C_2", "C_3", "C_4"]
    expected = end_points(structure, m, False, chains)

    chain = next(chain for chain in structure[0] if chain.name == "C")
    alpha = chain[0].find_atom("CA", "*")
    alpha.altloc = "A"
    other = gemmi.Atom()
    other.name, other.element, other.altloc = "CA", gemmi.Element("C"), "B"
    other.pos = gemmi.Position(alpha.pos.x + 30, alpha.pos.y, alpha.pos.z)
    chain[0].add_atom(other)
    ion = gemmi.Residue()
    ion.name, ion.het_flag = "CA", "H"
    atom = gemmi.Atom()
    atom.name, atom.element, atom.pos = "CA", gemmi.Element("Ca"), gemmi.Position(alpha.pos.x + 50, alpha.pos.y, alpha.pos.z)
    ion.add_atom(atom)
    chain.add_residue(ion)

    assert all(np.allclose(a, b) for a, b in zip(end_points(structure, m, False, chains), expected, strict=True))
