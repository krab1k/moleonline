"""Membrane position from OPM, moved into the coordinate frame of the computed structure.

OPM files contain the protein oriented with the membrane normal along z and two layers of
dummy atoms (residue DUM, atom names N and O) on the two membrane planes. The protein in the
OPM file is a rigidly moved copy of the PDB entry, so superposing it onto the computed
structure gives the transformation that moves the dummy atoms into the structure's frame.

Chains cannot be paired by their names: OPM renames chains of assemblies (A..L for four copies
of 1k4c) and PDBe names assembly copies like A, A_2. Alpha carbons are paired by residue number,
insertion code and residue name, chain against chain, under one common transformation.
"""

import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import gemmi
import numpy as np

OPM_URL = "https://opm-assets.storage.googleapis.com/pdb/{pdb_id}.pdb"

PAIR_RMSD = 1.0  # Å, a structure chain belongs to an OPM chain under the transformation
MAX_RMSD = 2.0  # Å, the superposition as a whole


class MembraneError(Exception):
    pass


@dataclass
class Membrane:
    points: list[tuple[np.ndarray, str]]  # position and side (N or O), in the structure's frame
    rmsd: float  # of the superposition, Å
    paired: int  # number of alpha carbons used


def download_opm(pdb_id: str, timeout: float = 60) -> str | None:
    """OPM file for the PDB entry, None when OPM does not have it."""
    try:
        with urllib.request.urlopen(OPM_URL.format(pdb_id=pdb_id.lower()), timeout=timeout) as response:
            return response.read().decode()
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):  # the storage answers 403 for missing objects
            return None
        raise


def read_structure(path: Path) -> gemmi.Structure:
    structure = gemmi.read_structure(str(path), format=gemmi.CoorFormat.Detect)  # uploads have extensions like .pdb1
    structure.setup_entities()
    return structure


def _alpha_carbons(model: gemmi.Model) -> dict[str, dict[tuple, np.ndarray]]:
    """Alpha carbons of each chain, keyed by residue number, insertion code and name."""
    chains: dict[str, dict[tuple, np.ndarray]] = {}
    for chain in model:
        atoms = {}
        for residue in chain:
            atom = residue.find_atom("CA", "*", gemmi.Element("C"))
            if atom is not None and residue.name != "DUM":
                atoms[(residue.seqid.num, residue.seqid.icode, residue.name)] = np.array(atom.pos.tolist())
        if len(atoms) >= 3:
            chains.setdefault(chain.name, {}).update(atoms)  # PDB files may split one chain
    return chains


def _kabsch(mobile: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rotation and translation moving mobile onto target (least squares)."""
    mobile_center, target_center = mobile.mean(axis=0), target.mean(axis=0)
    u, _, vt = np.linalg.svd((mobile - mobile_center).T @ (target - target_center))
    d = np.sign(np.linalg.det(vt.T @ u.T))
    rotation = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    return rotation, target_center - rotation @ mobile_center


def _rmsd(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.sqrt(((a - b) ** 2).sum(axis=1).mean()))


def _common(target: dict[tuple, np.ndarray], mobile: dict[tuple, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    keys = [k for k in target if k in mobile]
    return np.array([mobile[k] for k in keys]).reshape(-1, 3), np.array([target[k] for k in keys]).reshape(-1, 3)


def _pairing(structure_chains, opm_chains, rotation, translation):
    """Each structure chain with the closest unused OPM chain under the transformation."""
    pairs, used = [], set()
    for s_name, s_atoms in structure_chains.items():
        best = None
        for o_name, o_atoms in opm_chains.items():
            if o_name in used:
                continue
            mobile, target = _common(s_atoms, o_atoms)
            if len(mobile) < max(3, len(s_atoms) // 2):
                continue
            rmsd = _rmsd(mobile @ rotation.T + translation, target)
            if rmsd < PAIR_RMSD and (best is None or rmsd < best[0]):
                best = (rmsd, o_name, mobile, target)
        if best is not None:
            used.add(best[1])
            pairs.append((best[2], best[3]))
    return pairs


def superpose(structure: gemmi.Structure, opm: gemmi.Structure) -> tuple[np.ndarray, np.ndarray, float, int]:
    """Transformation moving the OPM file onto the structure, its RMSD and the number of paired atoms."""
    structure_chains = _alpha_carbons(structure[0])
    opm_chains = _alpha_carbons(opm[0])
    if not structure_chains or not opm_chains:
        raise MembraneError("The structure has no protein chains that could be paired with OPM.")

    # every OPM chain is tried as the partner of an anchor, the largest structure chain that has one
    # (complexes may contain chains missing in OPM); symmetric copies give the same membrane, the
    # first OPM chain wins ties
    best = None
    for anchor in sorted(structure_chains, key=lambda name: len(structure_chains[name]), reverse=True):
        for o_name in opm_chains:
            mobile, target = _common(structure_chains[anchor], opm_chains[o_name])
            if len(mobile) < max(3, len(structure_chains[anchor]) // 2):
                continue
            rotation, translation = _kabsch(mobile, target)
            pairs = _pairing(structure_chains, opm_chains, rotation, translation)
            if not pairs:
                continue
            mobile = np.vstack([m for m, _ in pairs])
            target = np.vstack([t for _, t in pairs])
            rotation, translation = _kabsch(mobile, target)
            rmsd = _rmsd(mobile @ rotation.T + translation, target)
            if best is None or len(mobile) > best[3]:
                best = (rotation, translation, rmsd, len(mobile))
        if best is not None:
            break
    if best is None:
        raise MembraneError("The structure could not be paired with its OPM entry.")
    if best[2] > MAX_RMSD:
        raise MembraneError(f"The structure differs from its OPM entry (RMSD {best[2]:.1f} Å).")
    return best


def membrane_from_opm(structure: gemmi.Structure, opm_text: str) -> Membrane:
    opm = gemmi.read_pdb_string(opm_text)
    rotation, translation, rmsd, paired = superpose(structure, opm)
    points = []
    for chain in opm[0]:
        for residue in chain:
            if residue.name == "DUM":
                for atom in residue:
                    position = rotation @ np.array(atom.pos.tolist()) + translation
                    points.append((position, atom.name))
    if not points:
        raise MembraneError("The OPM entry contains no membrane.")
    return Membrane(points, rmsd, paired)


def membrane_json(membrane: Membrane) -> list[dict]:
    """membrane.json as read by the web: positions and sides of the points on both membrane planes."""
    return [{"Position": {"X": float(p[0]), "Y": float(p[1]), "Z": float(p[2])}, "Side": side} for p, side in membrane.points]
