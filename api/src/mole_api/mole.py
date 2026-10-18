"""MOLE's input (input.xml) and the results of a submission (report.zip)"""

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .computation import Computation
from .parameters import MoleOrigin, MoleParameters, MoleResidue

INPUT = "input.xml"
PARAMETERS = "mole.json"  # what the user submitted
REPORT = "report.zip"


def element(tag: str, text: str | None = None, **attributes: str) -> ET.Element:
    e = ET.Element(tag, attributes)
    e.text = text
    return e


def load_input(c: Computation) -> Path:
    """The load (folder 0): MOLE reads the structure and writes its residues and chains (json/data.json), no tunnels"""
    directory = c.submit_directory(0)
    root = element("Tunnels")
    root.append(element("Input", str(c.structure_path())))
    root.append(element("WorkingDirectory", str(directory)))
    params = element("Params")
    params.append(element("Cavity", ProbeRadius="5", InteriorThreshold="1.1", IgnoreHETAtoms="0", IgnoreHydrogens="0"))
    root.append(params)
    export = element("Export")
    export.append(element("Formats", ChargeSurface="0", PyMol="0", PDBProfile="0", VMD="0", Chimera="0", CSV="0", JSON="1"))
    export.append(element("Types", Cavities="0", Tunnels="0", PoresAuto="0", PoresMerged="0", PoresUser="0"))
    root.append(export)
    root.append(element("Origins", Auto="0"))

    directory.mkdir(exist_ok=True)
    return write(root, directory / INPUT)


def number(value: float) -> str:
    """As the former C# API wrote it: 5, 1.25"""
    return repr(float(value)).removesuffix(".0")


def flag(value: bool) -> str:
    return "1" if value else "0"


def residues(items: list[MoleResidue]) -> list[ET.Element]:
    return [element("Residue", SequenceNumber=str(r.SequenceNumber), Chain=r.Chain) for r in items]


def origins(origin: MoleOrigin, tag: str, item: str) -> ET.Element:
    """<Origins Auto="1"/> without any; otherwise one item per point, per group of residues and for the query"""
    if origin.is_empty():
        return element(f"{tag}s", Auto="1")
    parent = element(f"{tag}s")
    for p in origin.Points or []:
        parent.append(child := element(item))
        child.append(element("Point", X=number(p.X), Y=number(p.Y), Z=number(p.Z)))
    for group in origin.Residues or []:
        parent.append(child := element(item))
        child.extend(residues(group))
    if origin.QueryExpression:
        parent.append(child := element(item))
        child.append(element("Query", origin.QueryExpression))
    return parent


def computation_input(c: Computation, p: MoleParameters) -> Path:
    """A tunnel computation of the last submission; only the first model of the structure is used. With exits and
    no origin, MOLE computes pores between the exits (PoresUser) instead of tunnels."""
    has_origin, has_exits = not p.Origin.is_empty(), not p.CustomExits.is_empty()
    structure = c.structure_path()
    directory = c.submit_directory(c.last.SubmitId)
    root = element("Tunnels")
    root.append(element("Input", str(structure), SpecificChains=p.Input.SpecificChains))
    root.append(element("WorkingDirectory", str(directory)))
    non_active = element("NonActiveParts")
    non_active.extend(residues(p.NonActiveResidues))
    if p.QueryFilter:
        non_active.append(element("Query", p.QueryFilter))
    root.append(non_active)
    params = element("Params")
    params.append(element("Cavity", ProbeRadius=number(p.Cavity.ProbeRadius), InteriorThreshold=number(p.Cavity.InteriorThreshold),
                          IgnoreHETAtoms=flag(p.Cavity.IgnoreHETAtoms), IgnoreHydrogens=flag(p.Cavity.IgnoreHydrogens)))
    params.append(element("Tunnel", BottleneckRadius=number(p.Tunnel.BottleneckRadius), BottleneckTolerance=number(p.Tunnel.BottleneckTolerance),
                          MaxTunnelSimilarity=number(p.Tunnel.MaxTunnelSimilarity), OriginRadius=number(p.Tunnel.OriginRadius),
                          SurfaceCoverRadius=number(p.Tunnel.SurfaceCoverRadius), WeightFunction=p.Tunnel.WeightFunction,
                          UseCustomExitsOnly=flag(has_origin and has_exits)))
    root.append(params)
    export = element("Export")
    export.append(element("Formats", ChargeSurface="0", PyMol="1", PDBProfile="1", VMD="1", Chimera="1", CSV="1", JSON="1"))
    export.append(element("Types", Cavities="0", Tunnels=flag(has_origin or not has_exits), PoresAuto=flag(p.PoresAuto),
                          PoresMerged=flag(p.PoresMerged), PoresUser=flag(has_exits and not has_origin)))
    for viewer in ("PyMol", "VMD", "Chimera"):
        export.append(element(viewer, PDBId=structure.stem, SurfaceType="Spheres"))
    root.append(export)
    root.append(origins(p.Origin, "Origin", "Origin"))
    if has_exits:
        root.append(origins(p.CustomExits, "CustomExit", "Exit"))
    return write(root, directory / INPUT)


def write(root: ET.Element, path: Path) -> Path:
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def zip_report(directory: Path, structure: Path) -> None:
    """report.zip in the submission's folder: the files of its subfolders (results of each format) and the structure.
    It appears only when complete."""
    temp = directory / f"{REPORT}.tmp"
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as report:
            for folder in sorted(p for p in directory.iterdir() if p.is_dir()):
                for file in sorted(f for f in folder.iterdir() if f.is_file()):
                    report.write(file, f"{folder.name}/{file.name}")
            report.write(structure, structure.name)
        temp.replace(directory / REPORT)
    finally:
        temp.unlink(missing_ok=True)
