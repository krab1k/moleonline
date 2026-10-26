"""The pore: input for a MOLE path along the membrane normal (the MOLE API runs MOLE with it), as Pores.exe
computed it.

The pore axis is parallel to the z axis of the membrane frame (see Membrane): x = y = 0, or through the centre of
the C-alpha atoms of the chosen chains. The path runs along it from the N side of the membrane to the O side:
- in the membrane: between the two membrane surfaces, where the axis crosses them (OPM models some membranes as
  curved; around the axis they are flat); MOLE moves the points to its nearest cavity points,
- otherwise: through the whole protein, MOLE's axis path (Axis="1") between two points far out on the axis; MOLE
  ends it where the protein ends along the axis, in the largest cavity.
"""

from pathlib import Path
from xml.etree import ElementTree as ET

import gemmi
import numpy as np

from .. import mole
from .membrane import Membrane

AXIS_RADIUS = 10.0  # Å, the membrane surfaces around the axis that it crosses
FLAT_AT_AXIS = 1.0  # Å, a membrane surface is flat around the axis when its points there differ less in height
FAR = 10000.0  # Å, the points of an axis path, far out of any structure


class PathError(Exception):
    pass


def _axis(structure: gemmi.Structure, membrane: Membrane, chains: list[str]) -> np.ndarray:
    """x and y of the pore axis in the membrane frame: through the centre of the chosen chains' C-alpha atoms, one per
    residue (not calcium ions, not every alternate conformation)"""
    if not chains:
        return np.zeros(2)
    alphas = [atom.pos.tolist() for chain in structure[0] if chain.name in chains for residue in chain
              if (atom := residue.find_atom("CA", "*", gemmi.Element("C"))) is not None]
    if not alphas:
        return np.zeros(2)
    return np.array([membrane.to_membrane_frame(p) for p in alphas]).mean(axis=0)[:2]


def _membrane_surfaces(membrane: Membrane, axis: np.ndarray) -> tuple[float, float]:
    """Heights of the N and O surfaces of the membrane on the axis, in the membrane frame."""
    if membrane.is_flat():
        return membrane.heights["N"].mean(), membrane.heights["O"].mean()
    near: dict[str, list[float]] = {"N": [], "O": []}
    for xyz, side in membrane.points:
        x, y, z = membrane.to_membrane_frame(xyz)
        if side in near and np.hypot(x - axis[0], y - axis[1]) < AXIS_RADIUS:
            near[side].append(z)
    if not near["N"] or not near["O"]:
        raise PathError("The membrane has no surface near the pore axis, the pore cannot be limited to the membrane region. "
                        "Compute it without the membrane region.")
    if any(np.ptp(z) > FLAT_AT_AXIS for z in near.values()):
        raise PathError("The membrane is not flat around the pore axis: the structure spans two membranes, "
                        "or the membrane is strongly curved. Compute the pore without the membrane region.")
    return float(np.mean(near["N"])), float(np.mean(near["O"]))


def end_points(structure: gemmi.Structure, membrane: Membrane, in_membrane: bool, chains: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """Start (N side) and end (O side) of the path, in the structure's frame: on the membrane surfaces, or far out
    on the axis (for an axis path)."""
    axis = _axis(structure, membrane, chains)
    if in_membrane:
        bottom, top = _membrane_surfaces(membrane, axis)
    else:
        bottom, top = (-FAR, FAR) if membrane.heights["N"].mean() < membrane.heights["O"].mean() else (FAR, -FAR)
    return membrane.to_structure([*axis, bottom]), membrane.to_structure([*axis, top])


def chains_only(structure: gemmi.Structure, structure_path: Path, chains: list[str], directory: Path) -> Path:
    """The structure MOLE computes with: only the chosen chains, as Pores.exe did (MOLE's SpecificChains keeps the
    other chains' atoms, where they change the path). Of the same name, which MOLE uses in the viewers' scripts."""
    if not chains:
        return structure_path
    only = structure.clone()
    for model in only:
        for i in reversed(range(len(model))):
            if model[i].name not in chains:
                del model[i]
    groups = gemmi.MmcifOutputGroups(True)
    groups.auth_all = True  # MOLE reads the author's names and numbers
    path = directory / f"{structure_path.stem}.cif"
    only.make_mmcif_document(groups).write_file(str(path))
    return path


def _point(xyz: np.ndarray) -> ET.Element:
    return mole.element("Point", **{axis: mole.number(round(float(v), 3)) for axis, v in zip("XYZ", xyz, strict=True)})


def write_mole_input(structure_path: Path, working_directory: Path, start: np.ndarray, end: np.ndarray,
                     probe_radius: float, interior_threshold: float, through: bool) -> Path:
    """MOLE's input (mole.INPUT in working_directory): the path from start to end, weighted by its length, through
    the protein without HET atoms and hydrogens; through: an axis path through the whole protein"""
    root = mole.element("Tunnels")
    root.append(mole.element("Input", str(structure_path), SpecificChains="", ReadAllModels="0"))
    root.append(mole.element("WorkingDirectory", str(working_directory)))
    params = mole.element("Params")
    params.append(mole.element("Cavity", ProbeRadius=mole.number(probe_radius), InteriorThreshold=mole.number(interior_threshold),
                               IgnoreHETAtoms="1", IgnoreHydrogens="1"))
    params.append(mole.element("Tunnel", WeightFunction="Length"))
    root.append(params)
    root.append(mole.export(structure_path))
    path = mole.element("Path", **({"Axis": "1"} if through else {}))
    for tag, xyz in (("Start", start), ("End", end)):
        path.append(parent := mole.element(tag))
        parent.append(_point(xyz))
    paths = mole.element("Paths")
    paths.append(path)
    root.append(paths)
    return mole.write(root, working_directory / mole.INPUT)
