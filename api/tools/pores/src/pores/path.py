"""The pore: input for a MOLE path along the membrane normal (the MOLE API runs MOLE with it).

The pore axis is the z axis of the OPM frame (OPM centres the protein on it). The path runs between
two points on the axis, from the N side of the membrane to the O side:
- in the membrane: on the two membrane planes,
- otherwise: where the protein ends along the axis (atoms within AXIS_RADIUS of it).
MOLE moves the points to its nearest cavity points, so paths start and end close to them.
"""

from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import gemmi
import numpy as np

from pores.membrane import Membrane

AXIS_RADIUS = 10.0  # Å, atoms around the axis that define where the protein ends


class PathError(Exception):
    pass


def _protein_atoms(structure: gemmi.Structure, chains: list[str]) -> np.ndarray:
    """Atoms MOLE computes with: no HET atoms (IgnoreHETAtoms), only the chosen chains."""
    return np.array([atom.pos.tolist()
                     for chain in structure[0] if not chains or chain.name in chains
                     for residue in chain if residue.het_flag != "H"
                     for atom in residue]).reshape(-1, 3)


def end_points(structure: gemmi.Structure, membrane: Membrane, in_membrane: bool, chains: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """Start (N side) and end (O side) of the path, in the structure's frame."""
    if in_membrane:
        if not membrane.is_flat():
            raise PathError("The membrane of this structure is curved (OPM), the pore in the membrane region is not supported yet.")
        bottom, top = membrane.heights["N"].mean(), membrane.heights["O"].mean()
    else:
        xyz = np.array([membrane.to_opm(p) for p in _protein_atoms(structure, chains)]).reshape(-1, 3)
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

