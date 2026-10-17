"""Membrane position from OPM, moved into the coordinate frame of the computed structure.

OPM files contain the protein oriented with the membrane normal along z and two layers of
dummy atoms (residue DUM, atom names N and O) on the two membrane planes. The protein in the
OPM file is a rigidly moved copy of the PDB entry, so superposing it onto the computed
structure gives the transformation that moves the dummy atoms into the structure's frame.

The dummy atoms are read from the text: OPM writes them after the END record in about a quarter of
its files, PDB readers (gemmi) stop at END.

Chains cannot be paired by their names: OPM renames chains of assemblies (A..L for four copies
of 1k4c) and PDBe names assembly copies like A, A_2. Alpha carbons are paired by residue number,
insertion code and residue name, chain against chain, under one common transformation.
"""

import http.client
import time
import urllib.error
import urllib.request
from pathlib import Path

import gemmi
import numpy as np

from pores.membrane import Membrane, MembraneError, kabsch, rmsd

OPM_URL = "https://opm-assets.storage.googleapis.com/pdb/{pdb_id}.pdb"
OPM_TIMEOUT = 30  # s, per network operation
CACHE_DAYS = 30  # OPM revises entries rarely (e.g. curved membranes)
PAIR_RMSD = 1.0  # Å, a structure chain belongs to an OPM chain under the transformation
MAX_RMSD = 2.0  # Å, the superposition as a whole


def download_opm(pdb_id: str, cache: Path | None = None) -> str | None:
    """OPM file for the PDB entry, None when OPM does not have it.

    Downloaded files are kept in the cache directory for CACHE_DAYS; when OPM is not available,
    an older cached file is used rather than failing.
    """
    pdb_id = pdb_id.lower()
    cached = cache / f"{pdb_id}.pdb" if cache else None
    if cached and cached.exists() and time.time() - cached.stat().st_mtime < CACHE_DAYS * 86400:
        return cached.read_text()
    try:
        with urllib.request.urlopen(OPM_URL.format(pdb_id=pdb_id), timeout=OPM_TIMEOUT) as response:
            text = response.read().decode()
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):  # the storage answers 403 for missing objects
            return None
        if cached and cached.exists():
            return cached.read_text()
        raise MembraneError(f"OPM returned an error ({e.code}) when downloading the membrane position of {pdb_id}. Please, try again later.")
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
        if cached and cached.exists():
            return cached.read_text()
        raise MembraneError("The membrane position could not be downloaded, OPM (opm-assets.storage.googleapis.com) is not available at the moment. Please, try again later.") from e
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        temp = cached.with_suffix(f".{time.time_ns()}.tmp")  # the cached file appears only when complete
        temp.write_text(text)
        temp.replace(cached)
    return text


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
            fit = rmsd(mobile @ rotation.T + translation, target)
            if fit < PAIR_RMSD and (best is None or fit < best[0]):
                best = (fit, o_name, mobile, target)
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
            rotation, translation = kabsch(mobile, target)
            pairs = _pairing(structure_chains, opm_chains, rotation, translation)
            if not pairs:
                continue
            mobile = np.vstack([m for m, _ in pairs])
            target = np.vstack([t for _, t in pairs])
            rotation, translation = kabsch(mobile, target)
            fit = rmsd(mobile @ rotation.T + translation, target)
            if best is None or len(mobile) > best[3]:
                best = (rotation, translation, fit, len(mobile))
        if best is not None:
            break
    if best is None:
        raise MembraneError("The structure could not be paired with its OPM entry.")
    if best[2] > MAX_RMSD:
        raise MembraneError(f"The structure differs from its OPM entry (RMSD {best[2]:.1f} Å).")
    return best


def _dummy_atoms(opm_text: str) -> list[tuple[np.ndarray, str]]:
    """Dummy atoms of an OPM file, positions in the OPM frame and names (N, O), wherever they are."""
    return [(np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])]), line[12:16].strip())
            for line in opm_text.splitlines()
            if line.startswith(("HETATM", "ATOM")) and line[17:20] == "DUM"]


def membrane_from_opm(structure: gemmi.Structure, opm_text: str) -> Membrane:
    opm = gemmi.read_pdb_string(opm_text)
    rotation, translation, fit, paired = superpose(structure, opm)
    points, heights = [], {"N": [], "O": []}
    for xyz, name in _dummy_atoms(opm_text):
        points.append((rotation @ xyz + translation, name))
        heights.setdefault(name, []).append(xyz[2])
    if not heights["N"] or not heights["O"]:
        raise MembraneError("The OPM entry contains no membrane.")
    return Membrane(points, fit, paired, rotation, translation, {side: np.array(h) for side, h in heights.items()})
