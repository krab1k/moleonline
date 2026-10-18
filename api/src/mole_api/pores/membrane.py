"""The membrane as both sources (OPM, MEMEMBED) provide it: dots on the two membrane planes, moved
into the coordinate frame of the computed structure, and shared helpers."""

from dataclasses import dataclass
from pathlib import Path

import gemmi
import numpy as np


class MembraneError(Exception):
    pass


@dataclass
class Membrane:
    """Membrane from OPM or MEMEMBED. In the membrane frame the membrane normal is the z axis and the
    pore axis is x = y = 0 (OPM centres the protein on it)."""
    points: list[tuple[np.ndarray, str]]  # position and side (N or O), in the structure's frame
    rmsd: float  # of the superposition, Å
    paired: int  # number of atoms used
    rotation: np.ndarray  # membrane frame -> structure frame
    translation: np.ndarray
    heights: dict[str, np.ndarray]  # z of the points of each side in the membrane frame

    def to_structure(self, frame_xyz) -> np.ndarray:
        return self.rotation @ np.asarray(frame_xyz, dtype=float) + self.translation

    def to_membrane_frame(self, xyz) -> np.ndarray:
        return self.rotation.T @ (np.asarray(xyz, dtype=float) - self.translation)

    def is_finite(self) -> bool:
        """No NaN or infinity anywhere, e.g. from a superposition of too few atoms"""
        return bool(np.isfinite(self.rmsd) and np.isfinite(self.rotation).all() and np.isfinite(self.translation).all()
                    and all(np.isfinite(p).all() for p, _ in self.points) and all(np.isfinite(h).all() for h in self.heights.values()))

    def is_flat(self) -> bool:
        """Two parallel planes; OPM models some membranes as curved surfaces."""
        return all(np.ptp(h) < 0.01 for h in self.heights.values())


def read_structure(path: Path) -> gemmi.Structure:
    structure = gemmi.read_structure(str(path), format=gemmi.CoorFormat.Detect)  # uploads have extensions like .pdb1
    structure.setup_entities()
    return structure


def kabsch(mobile: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rotation and translation moving mobile onto target (least squares)."""
    mobile_center, target_center = mobile.mean(axis=0), target.mean(axis=0)
    u, _, vt = np.linalg.svd((mobile - mobile_center).T @ (target - target_center))
    d = np.sign(np.linalg.det(vt.T @ u.T))
    rotation = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    return rotation, target_center - rotation @ mobile_center


def rmsd(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.sqrt(((a - b) ** 2).sum(axis=1).mean()))


def membrane_json(membrane: Membrane) -> list[dict]:
    """membrane.json as read by the web: positions and sides of the points on both membrane planes."""
    return [{"Position": {"X": float(p[0]), "Y": float(p[1]), "Z": float(p[2])}, "Side": side} for p, side in membrane.points]
