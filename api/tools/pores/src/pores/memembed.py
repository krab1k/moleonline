"""Membrane position predicted by MEMEMBED, for uploaded structures and entries missing in OPM.

MEMEMBED (Nugent & Jones, BMC Bioinformatics 2013) reads a PDB file and writes it rotated so that
the membrane normal is the z axis, with dummy atoms (DUM, names N and O) on both membrane planes,
like OPM. The protein atoms keep their order, so superposing them onto the input gives the
transformation back into the structure's frame.

Settings of the original Pores: thickness optimised after the orientation (-q 1). The search is
the deterministic direct search (-s 2): the same structure always gives the same membrane.
"""

import subprocess
import tempfile
from pathlib import Path

import gemmi
import numpy as np

from pores.membrane import Membrane, MembraneError, kabsch, rmsd


def _atoms(structure: gemmi.Structure) -> np.ndarray:
    return np.array([atom.pos.tolist() for chain in structure[0] for residue in chain
                     if residue.name != "DUM" for atom in residue]).reshape(-1, 3)


def membrane_from_memembed(structure: gemmi.Structure, memembed: str, beta_barrel: bool, chains: list[str]) -> Membrane:
    protein = structure.clone()
    protein.remove_ligands_and_waters()
    protein.remove_hydrogens()
    if chains:
        for name in {chain.name for chain in protein[0]} - set(chains):
            protein[0].remove_chain(name)
    protein.remove_empty_chains()
    if len(_atoms(protein)) == 0:
        raise MembraneError("The structure contains no protein to place into a membrane.")
    protein.shorten_chain_names()  # PDB files have one-letter chains, PDBe assemblies use A_2 etc.

    with tempfile.TemporaryDirectory(prefix="memembed") as directory:
        input_path, output_path = Path(directory) / "protein.pdb", Path(directory) / "membrane.pdb"
        protein.write_pdb(str(input_path))
        command = [memembed, "-s", "2", "-q", "1", *(["-b"] if beta_barrel else []), "-o", str(output_path), str(input_path)]
        result = subprocess.run(command, capture_output=True, text=True)  # MEMEMBED writes only the -o file
        if result.returncode != 0 or not output_path.exists():
            raise MembraneError(f"MEMEMBED could not place the structure into a membrane: {(result.stderr or result.stdout).strip()[-500:]}")
        source = _atoms(gemmi.read_structure(str(input_path)))
        placed = gemmi.read_structure(str(output_path))

    moved = _atoms(placed)
    if moved.shape != source.shape:
        raise MembraneError("MEMEMBED changed the atoms of the structure.")
    rotation, translation = kabsch(moved, source)  # MEMEMBED frame -> structure frame
    fit = rmsd(moved @ rotation.T + translation, source)

    heights: dict[str, list[float]] = {"N": [], "O": []}
    dummies = []
    for chain in placed[0]:
        for residue in chain:
            if residue.name == "DUM":
                for atom in residue:
                    dummies.append((np.array(atom.pos.tolist()), atom.name))
                    heights.setdefault(atom.name, []).append(atom.pos.z)
    if not heights["N"] or not heights["O"]:
        raise MembraneError("MEMEMBED placed no membrane.")

    # MEMEMBED does not centre the protein on its z axis like OPM: put the pore axis through the
    # centre of the protein inside the membrane
    bottom, top = min(np.mean(h) for h in heights.values()), max(np.mean(h) for h in heights.values())
    inside = moved[(moved[:, 2] > bottom) & (moved[:, 2] < top)]
    center = (inside if len(inside) else moved).mean(axis=0) * [1.0, 1.0, 0.0]
    translation = translation + rotation @ center

    points = [(rotation @ (xyz - center) + translation, side) for xyz, side in dummies]
    return Membrane(points, fit, len(moved), rotation, translation, {side: np.array(h) for side, h in heights.items()})
