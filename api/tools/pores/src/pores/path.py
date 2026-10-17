"""The pore: input for a MOLE path along the membrane normal (the MOLE API runs MOLE with it).

The pore axis is the z axis of the membrane frame (x = y = 0, see Membrane). The path runs between
two points on the axis, from the N side of the membrane to the O side:
- in the membrane: on the two membrane surfaces, where the axis crosses them (OPM models some
  membranes as curved; around the axis they are flat),
- otherwise: where the protein ends along the axis (atoms within AXIS_RADIUS of it).
MOLE moves the points to its nearest cavity points, so paths start and end close to them.
"""

from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import gemmi
import numpy as np

from pores.membrane import Membrane

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


def _point(xyz: np.ndarray) -> str:
    return f'<Point X="{xyz[0]:.3f}" Y="{xyz[1]:.3f}" Z="{xyz[2]:.3f}" />'


def mole_input(structure_path: Path, working_directory: Path, start: np.ndarray, end: np.ndarray,
               probe_radius: float, interior_threshold: float, chains: list[str]) -> str:
    name = quoteattr(structure_path.stem)
    return f"""<Tunnels>
  <Input SpecificChains={quoteattr(",".join(chains))} ReadAllModels="0">{escape(str(structure_path))}</Input>
  <WorkingDirectory>{escape(str(working_directory))}</WorkingDirectory>
  <Params>
    <Cavity ProbeRadius="{probe_radius}" InteriorThreshold="{interior_threshold}" IgnoreHETAtoms="1" IgnoreHydrogens="0" />
    <Tunnel WeightFunction="Length" />
  </Params>
  <Export>
    <Formats ChargeSurface="0" PyMol="1" PDBProfile="1" VMD="1" Chimera="1" CSV="1" JSON="1" />
    <Types Cavities="0" Tunnels="0" PoresAuto="0" PoresMerged="0" PoresUser="0" />
    <PyMol PDBId={name} SurfaceType="Spheres" />
    <VMD PDBId={name} SurfaceType="Spheres" />
    <Chimera PDBId={name} SurfaceType="Spheres" />
  </Export>
  <Paths>
    <Path>
      <Start>{_point(start)}</Start>
      <End>{_point(end)}</End>
    </Path>
  </Paths>
</Tunnels>
"""

