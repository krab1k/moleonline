"""Results of a submission for download (/Data): MOLE's files in the submission's folder, the structure, the membrane"""

import io
import zipfile
from pathlib import Path

from fastapi.responses import FileResponse, Response

from .computation import Computation

JSON, ZIP, TEXT = "application/json", "application/zip", "text/plain"
FORMATS = ["json", "report", "molecule", "pymol", "chimera", "vmd", "pdb", "membrane"]


def download(c: Computation, submit_id: int, format: str) -> Response | None:
    """The file to download; None when it is not available (a running or failed submission, no membrane outside pore
    computations)"""
    folder, name = c.submit_directory(submit_id), f"mole_channels_{c.ComputationId}_{submit_id}"
    match format:
        case "json":
            path, media_type, name = folder / "json" / "data.json", JSON, f"{name}.json"
        case "report":
            path, media_type, name = folder / "report.zip", ZIP, f"{name}.zip"
        case "molecule":
            if c.StructureFile is None:
                return None
            path, media_type, name = c.structure_path(), TEXT, c.StructureFile
        case "pymol":
            path, media_type, name = folder / "pymol" / "complex.py", TEXT, f"{name}_pymol.py"
        case "chimera":
            path, media_type, name = folder / "chimera" / "complex.py", TEXT, f"{name}_chimera.py"
        case "vmd":
            path, media_type, name = folder / "vmd" / "complex", TEXT, f"{name}.tk"
        case "pdb":  # the tunnels' profiles, a folder
            profiles = folder / "pdb" / "profile"
            if not profiles.is_dir():
                return None
            return Response(zip_folder(profiles), media_type=ZIP, headers=attachment(f"{name}_pdb.zip"))
        case "membrane":  # pore computations
            path, media_type, name = c.folder() / "membrane.json", JSON, "membrane.json"
        case _:
            raise ValueError(f"Unknown format '{format}'")
    return FileResponse(path, media_type=media_type, headers=attachment(name)) if path.is_file() else None


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
