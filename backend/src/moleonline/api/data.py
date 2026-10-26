"""Files of a session for download: its structure, what its load found, its jobs' results (stored by the worker in the
job's folder; the formats other than data.json are read from the report)"""

import io
import zipfile
from enum import StrEnum
from pathlib import Path

from fastapi.responses import FileResponse, Response

from ..core.storage import JOB_DATA, JOB_MEMBRANE, JOB_REPORT, StoredSession

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


def structure(s: StoredSession) -> Response | None:
    """The session's structure, as stored (a gzipped upload unpacked)"""
    return None if s.structure_file is None else file(s.structure_path(), TEXT, s.structure_file)


def session_data(s: StoredSession) -> Response | None:
    """MOLE's data.json of the load: the cavities and the origins it found"""
    return file(s.load_data(), JSON, f"mole_{s.id}.json")


def job_result(s: StoredSession, number: int, format: ResultFormat) -> Response | None:
    """A result of the job; None when it is not available (no profiles, no membrane outside pore jobs)"""
    folder, name = s.job_directory(number), f"mole_channels_{s.id}_{number}"
    match format:
        case ResultFormat.data:
            return file(folder / JOB_DATA, JSON, f"{name}.json")
        case ResultFormat.report:
            return file(folder / JOB_REPORT, ZIP, f"{name}.zip")
        case ResultFormat.pymol:
            return reported(folder, "pymol/complex.py", TEXT, f"{name}_pymol.py")
        case ResultFormat.chimera:
            return reported(folder, "chimera/complex.py", TEXT, f"{name}_chimera.py")
        case ResultFormat.vmd:
            return reported(folder, "vmd/complex", TEXT, f"{name}.tk")
        case ResultFormat.pdb:  # the tunnels' profiles, a folder
            profiles = from_report(folder, "pdb/profile/")
            return Response(zipped(profiles), media_type=ZIP, headers=attachment(f"{name}_pdb.zip")) if profiles else None
        case ResultFormat.membrane:  # written by the pores tool
            return file(folder / JOB_MEMBRANE, JSON, "membrane.json")


def from_report(folder: Path, prefix: str) -> dict[str, bytes]:
    """The files of the job's report whose names start with prefix, by their names without it"""
    try:
        with zipfile.ZipFile(folder / JOB_REPORT) as report:
            return {i.filename.removeprefix(prefix): report.read(i) for i in report.infolist()
                    if i.filename.startswith(prefix) and not i.is_dir()}
    except FileNotFoundError:
        return {}


def reported(folder: Path, name: str, media_type: str, download: str) -> Response | None:
    content = from_report(folder, name).get("")
    return None if content is None else Response(content, media_type=media_type, headers=attachment(download))


def attachment(name: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{name}"'}


def zipped(files: dict[str, bytes]) -> bytes:
    """In memory, so that concurrent downloads do not share a file"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
    return buffer.getvalue()
