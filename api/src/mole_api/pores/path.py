"""The pore: input for a MOLE path along the membrane normal (the MOLE API runs MOLE with it).

The pore axis is the z axis of the membrane frame (x = y = 0, see Membrane). The path runs between
two points on the axis, from the N side of the membrane to the O side:
- in the membrane: on the two membrane surfaces, where the axis crosses them (OPM models some
  membranes as curved; around the axis they are flat),
- otherwise: where the protein ends along the axis (atoms within AXIS_RADIUS of it).
MOLE moves the points to its nearest cavity points, so paths start and end close to them.
"""

from pathlib import Path
from xml.etree import ElementTree as ET

import gemmi
import numpy as np

from .. import mole
from .membrane import Membrane

AXIS_RADIUS = 10.0  # Å, atoms around the axis that define where the protein ends, the membrane surfaces it crosses
FLAT_AT_AXIS = 1.0  # Å, a membrane surface is flat around the axis when its points there differ less in height


class PathError(Exception):
    pass


def _protein_atoms(structure: gemmi.Structure, chains: list[str]) -> np.ndarray:
    """Atoms MOLE computes with: no HET atoms (IgnoreHETAtoms), only the chosen chains."""
    return np.array([atom.pos.tolist()
                     for chain in structure[0] if not chains or chain.name in chains
                     for residue in chain if residue.het_flag != "H"
                     for atom in residue]).reshape(-1, 3)


def _membrane_surfaces(membrane: Membrane) -> tuple[float, float]:
    """Heights of the N and O surfaces of the membrane on the axis, in the membrane frame."""
    if membrane.is_flat():
        return membrane.heights["N"].mean(), membrane.heights["O"].mean()
    near: dict[str, list[float]] = {"N": [], "O": []}
    for xyz, side in membrane.points:
        x, y, z = membrane.to_membrane_frame(xyz)
        if side in near and np.hypot(x, y) < AXIS_RADIUS:
            near[side].append(z)
    if not near["N"] or not near["O"]:
        raise PathError("The membrane has no surface near the pore axis, the pore cannot be limited to the membrane region. "
                        "Compute it without the membrane region.")
    if any(np.ptp(z) > FLAT_AT_AXIS for z in near.values()):
        raise PathError("The membrane is not flat around the pore axis: the structure spans two membranes, or the membrane is strongly curved. "
                        "Compute the pore without the membrane region.")
    return float(np.mean(near["N"])), float(np.mean(near["O"]))


def end_points(structure: gemmi.Structure, membrane: Membrane, in_membrane: bool, chains: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """Start (N side) and end (O side) of the path, in the structure's frame."""
    if in_membrane:
        bottom, top = _membrane_surfaces(membrane)
    else:
        xyz = np.array([membrane.to_membrane_frame(p) for p in _protein_atoms(structure, chains)]).reshape(-1, 3)
        near = xyz[np.hypot(xyz[:, 0], xyz[:, 1]) < AXIS_RADIUS]
        if len(near) == 0:
            raise PathError("The protein does not surround the membrane normal, no pore can pass through it.")
        bottom, top = near[:, 2].min(), near[:, 2].max()
        if membrane.heights["N"].mean() > membrane.heights["O"].mean():
            bottom, top = top, bottom
    return membrane.to_structure([0.0, 0.0, bottom]), membrane.to_structure([0.0, 0.0, top])


def _point(xyz: np.ndarray) -> ET.Element:
    return mole.element("Point", **{axis: mole.number(round(float(v), 3)) for axis, v in zip("XYZ", xyz)})


def write_mole_input(structure_path: Path, working_directory: Path, start: np.ndarray, end: np.ndarray,
                     probe_radius: float, interior_threshold: float, chains: list[str]) -> Path:
    """MOLE's input (mole.INPUT in working_directory): the path from start to end, weighted by its length, through
    the protein without HET atoms"""
    root = mole.element("Tunnels")
    root.append(mole.element("Input", str(structure_path), SpecificChains=",".join(chains), ReadAllModels="0"))
    root.append(mole.element("WorkingDirectory", str(working_directory)))
    params = mole.element("Params")
    params.append(mole.element("Cavity", ProbeRadius=mole.number(probe_radius), InteriorThreshold=mole.number(interior_threshold),
                               IgnoreHETAtoms="1", IgnoreHydrogens="0"))
    params.append(mole.element("Tunnel", WeightFunction="Length"))
    root.append(params)
    root.append(mole.export(structure_path))
    path = mole.element("Path")
    for tag, xyz in (("Start", start), ("End", end)):
        path.append(parent := mole.element(tag))
        parent.append(_point(xyz))
    paths = mole.element("Paths")
    paths.append(path)
    root.append(paths)
    return mole.write(root, working_directory / mole.INPUT)

