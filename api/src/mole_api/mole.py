"""MOLE's input files (input.xml)"""

from pathlib import Path
from xml.etree import ElementTree as ET

from .computation import Computation

INPUT = "input.xml"


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
    path = directory / INPUT
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path
