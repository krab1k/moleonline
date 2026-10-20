"""Files of a session for download: its structure, what its load found, its jobs' results (MOLE's files in the job's folder)"""

import io
import zipfile
from enum import StrEnum
from pathlib import Path

from fastapi.responses import FileResponse, Response

from .computation import Computation

JSON, ZIP, TEXT = "application/json", "application/zip", "text/plain"


class ResultFormat(StrEnum):
    """A job's results: MOLE's data.json (what the web shows), all of them zipped, scripts of the viewers, the tunnels'
    profiles (a zip of PDB files), the membrane of a pore job"""
    data = "data"
    report = "report"
    pymol = "pymol"
    chimera = "chimera"
    vmd = "vmd"
    pdb = "pdb"
    membrane = "membrane"


def file(path: Path, media_type: str, name: str) -> Response | None:
    return FileResponse(path, media_type=media_type, headers=attachment(name)) if path.is_file() else None


def structure(c: Computation) -> Response | None:
    """The session's structure, as stored (a gzipped upload unpacked)"""
    return None if c.StructureFile is None else file(c.structure_path(), TEXT, c.StructureFile)


def session_data(c: Computation) -> Response | None:
    """MOLE's data.json of the load: the cavities and the origins it found"""
    return file(c.submit_directory(0) / "json" / "data.json", JSON, f"mole_{c.ComputationId}.json")


def job_result(c: Computation, number: int, format: ResultFormat) -> Response | None:
    """A result of the job; None when it is not available (no profiles, no membrane outside pore jobs)"""
    folder, name = c.submit_directory(number), f"mole_channels_{c.ComputationId}_{number}"
    match format:
        case ResultFormat.data:
            return file(folder / "json" / "data.json", JSON, f"{name}.json")
        case ResultFormat.report:
            return file(folder / "report.zip", ZIP, f"{name}.zip")
        case ResultFormat.pymol:
            return file(folder / "pymol" / "complex.py", TEXT, f"{name}_pymol.py")
        case ResultFormat.chimera:
            return file(folder / "chimera" / "complex.py", TEXT, f"{name}_chimera.py")
        case ResultFormat.vmd:
            return file(folder / "vmd" / "complex", TEXT, f"{name}.tk")
        case ResultFormat.pdb:  # the tunnels' profiles, a folder
            profiles = folder / "pdb" / "profile"
            return Response(zip_folder(profiles), media_type=ZIP, headers=attachment(f"{name}_pdb.zip")) if profiles.is_dir() else None
        case ResultFormat.membrane:  # written by the pores tool
            return file(folder / "membrane.json", JSON, "membrane.json")


def attachment(name: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{name}"'}


def zip_folder(path: Path) -> bytes:
    """In memory, so that concurrent downloads do not share a file"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(path.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(path).as_posix())
    return buffer.getvalue()
