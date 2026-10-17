"""The membrane computed from OPM against production results of Pores.exe (see reference/README.md)."""

import json
from pathlib import Path

import gemmi
import numpy as np
import pytest

from pores.membrane import kabsch, membrane_json, read_structure
from pores.opm import membrane_from_opm

REFERENCE = Path(__file__).parent.parent / "reference"


def points(membrane: list[dict]) -> tuple[np.ndarray, list[str]]:
    return np.array([[p["Position"][k] for k in "XYZ"] for p in membrane]), [p["Side"] for p in membrane]


def compute(pdb_id: str) -> list[dict]:
    structure = read_structure(REFERENCE / pdb_id / "data_molecule")
    return membrane_json(membrane_from_opm(structure, (REFERENCE / pdb_id / f"opm_{pdb_id}.pdb").read_text()))


def reference(pdb_id: str) -> list[dict]:
    return json.loads((REFERENCE / pdb_id / "data_membrane").read_text())


@pytest.mark.parametrize("pdb_id", ["2vv5", "3ldc"])
def test_same_points(pdb_id):
    ours, ours_sides = points(compute(pdb_id))
    theirs, theirs_sides = points(reference(pdb_id))
    assert ours_sides == theirs_sides
    assert np.linalg.norm(ours - theirs, axis=1).max() < 0.01


def test_1k4c_same_membrane_up_to_symmetry():
    """Production superposed a different, equally fitting copy of the 4-fold symmetric channel:
    the same dots, rotated around the membrane normal (the pore axis)."""
    ours, ours_sides = points(compute("1k4c"))
    theirs, theirs_sides = points(reference("1k4c"))
    assert ours_sides == theirs_sides

    # the same OPM dots under two transformations: one rigid motion maps ours exactly onto theirs
    rotation, translation = kabsch(ours, theirs)
    assert np.linalg.norm(ours @ rotation.T + translation - theirs, axis=1).max() < 0.01

    # and it is a rotation around the membrane normal, so the membrane planes are the same
    sides = np.array(theirs_sides)
    normal = theirs[sides == "O"].mean(axis=0) - theirs[sides == "N"].mean(axis=0)
    axis = np.array([rotation[2, 1] - rotation[1, 2], rotation[0, 2] - rotation[2, 0], rotation[1, 0] - rotation[0, 1]])
    assert abs(np.dot(axis / np.linalg.norm(axis), normal / np.linalg.norm(normal))) > 0.9999


def test_largest_chain_missing_in_opm():
    """A complex may contain a chain OPM does not have; the other chains still give the membrane."""
    structure = read_structure(REFERENCE / "2vv5" / "data_molecule")
    extra = gemmi.Chain("X")
    for i in range(1000):  # larger than any 2VV5 chain, residues OPM does not know
        residue = gemmi.Residue()
        residue.name, residue.seqid = "ALA", gemmi.SeqId(5000 + i, " ")
        atom = gemmi.Atom()
        atom.name, atom.element, atom.pos = "CA", gemmi.Element("C"), gemmi.Position(500 + 3.8 * i, 0, 0)
        residue.add_atom(atom)
        extra.add_residue(residue)
    structure[0].add_chain(extra)
    ours, ours_sides = points(membrane_json(membrane_from_opm(structure, (REFERENCE / "2vv5" / "opm_2vv5.pdb").read_text())))
    theirs, theirs_sides = points(reference("2vv5"))
    assert ours_sides == theirs_sides
    assert np.linalg.norm(ours - theirs, axis=1).max() < 0.01


def test_dummy_atoms_after_end():
    """5ng5: OPM writes the dummy atoms after the END record (a quarter of OPM files), gemmi stops reading
    there. All are read; the OPM file serves as the structure (no production reference for it)."""
    opm_path = REFERENCE / "opm_only" / "opm_5ng5.pdb"
    lines = opm_path.read_text().splitlines()
    end = next(i for i, line in enumerate(lines) if line.strip() == "END")
    after = [line[12:16].strip() for line in lines[end:] if line[17:20] == "DUM"]
    assert after.count("N") > 0 and after.count("O") > 0
    m = membrane_from_opm(read_structure(opm_path), opm_path.read_text())
    assert [len(m.heights[side]) for side in "NO"] == [after.count("N"), after.count("O")]
    assert m.rmsd < 0.01
