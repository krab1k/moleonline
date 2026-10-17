"""An uploaded structure: one PDB or mmCIF file, optionally gzipped, at most 30 MB also when unpacked"""

import contextlib
import gzip
import re
import zlib
from pathlib import Path
from typing import BinaryIO

MAX_STRUCTURE_MB = 30  # also of a structure downloaded from PDBe
MB = 1024 * 1024
MAX_REQUEST_BYTES = (MAX_STRUCTURE_MB + 1) * MB  # the file and the form around it; the file is checked when stored
STRUCTURE_NAME = re.compile(r"\.(pdb|cif)(\.gz)?$")  # MOLE and the web use the first model

TOO_LARGE = f"The file is too large, at most {MAX_STRUCTURE_MB} MB can be uploaded. Gzipped files (*.pdb.gz, *.cif.gz) are accepted."
UNPACKS_TOO_LARGE = f"The file unpacks to more than {MAX_STRUCTURE_MB} MB, at most {MAX_STRUCTURE_MB} MB of structure can be computed."
NOT_GZIP = "The file is not a valid gzip file or it is incomplete."
NO_FILE = "No structure file was uploaded or the file is empty."
UNSUPPORTED = "Unsupported file type. Supported are PDB (*.pdb) and mmCIF (*.cif) files, also gzipped (*.pdb.gz, *.cif.gz)."


def store(source: BinaryIO, gzipped: bool, target: Path) -> tuple[int, str] | None:
    """Copies the structure to target, unpacked; returns the HTTP status and the reason when it is refused (target
    then holds a part). gzip checks the whole file: a truncated or corrupt one fails, several members are read."""
    try:
        with gzip.GzipFile(fileobj=source, mode="rb") if gzipped else contextlib.nullcontext(source) as data, target.open("wb") as out:
            size = 0
            while chunk := data.read(MB):
                size += len(chunk)
                if size > MAX_STRUCTURE_MB * MB:
                    return 413, UNPACKS_TOO_LARGE if gzipped else TOO_LARGE
                out.write(chunk)
    except (gzip.BadGzipFile, EOFError, zlib.error):
        return 400, NOT_GZIP
    return None
