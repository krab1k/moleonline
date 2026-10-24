"""Structures of PDB entries from PDBe: the model server (https://www.ebi.ac.uk/pdbe/model-server) builds the asymmetric
unit or an assembly; the preferred assembly is the one OPM has (pores of a PDB entry)."""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx2

from ..core.entries import ASYMMETRIC, PREFERRED
from ..core.limits import MAX_STRUCTURE_MB, MB


def client() -> httpx2.AsyncClient:
    # the model server builds assemblies on the fly: up to 120 s for the response, and between its parts
    return httpx2.AsyncClient(base_url="https://www.ebi.ac.uk/pdbe", timeout=httpx2.Timeout(120, connect=15))


class Assemblies:
    """The assemblies of an entry, by their ids, and the preferred one"""

    def __init__(self, ids: list[str], preferred: str | None):
        self.ids, self.preferred = ids, preferred


async def assemblies(http: httpx2.AsyncClient, pdb_id: str) -> Assemblies | None:
    """None when they cannot be looked up"""
    try:
        response = await http.get(f"/static/entry/download/{pdb_id}-assembly.xml")
        if response.status_code != 200:
            return None
        found = [a for a in ET.fromstring(response.content).iter("assembly") if a.get("id")]
        return Assemblies([a.get("id") for a in found], next((a.get("id") for a in found if a.get("prefered") == "True"), None))
    except (httpx2.HTTPError, ET.ParseError):
        return None


def no_assembly(pdb_id: str, assembly: str, known: Assemblies | None) -> str:
    listed = f" Its assemblies are {', '.join(known.ids)}." if known and known.ids else ""
    return f"Structure [{pdb_id}] has no assembly [{assembly}].{listed}"


@dataclass(frozen=True)
class Downloaded:
    """A structure downloaded into a session's folder"""
    structure_file: str
    assembly_id: str | None  # None: the asymmetric unit
    opm: bool  # the preferred assembly, the one OPM has


@dataclass(frozen=True)
class NotDownloaded:
    error: str
    assembly_id: str | None  # the assembly that was looked for


async def download(http: httpx2.AsyncClient, folder: Path, pdb_id: str, assembly: str) -> Downloaded | NotDownloaded:
    """Downloads the structure into the folder, or says why it failed. assembly: PREFERRED (the asymmetric unit when
    it cannot be looked up), ASYMMETRIC or an assembly id."""
    known = None if assembly == ASYMMETRIC else await assemblies(http, pdb_id)
    assembly_id = None if assembly == ASYMMETRIC else (known and known.preferred) if assembly == PREFERRED else assembly
    if known is not None and assembly_id is not None and assembly_id not in known.ids:
        return NotDownloaded(no_assembly(pdb_id, assembly_id, known), assembly_id)
    url = f"/model-server/v1/{pdb_id}/assembly?name={assembly_id}" if assembly_id else f"/model-server/v1/{pdb_id}/full"
    file = folder / f"{pdb_id}.cif"
    temp = file.with_name(file.name + ".download")  # the structure file appears only when complete
    failed = None
    try:
        status = await fetch(http, url, temp)
        if status == 413:
            failed = f"Structure [{pdb_id}] is larger than {MAX_STRUCTURE_MB} MB, at most {MAX_STRUCTURE_MB} MB can be computed."
        elif status == 404:
            failed = f"Structure [{pdb_id}] is unlikely to exist or has been made obsolete."
        elif status != 200:
            failed = f"PDBe returned an error ({status}) when downloading structure [{pdb_id}]. Please, try again later."
        else:
            temp.rename(file)
            error = await asyncio.to_thread(server_error, file)
            if error is not None:
                # the assemblies could not be looked up before
                undefined = UNDEFINED_ASSEMBLY.search(error)
                failed = no_assembly(pdb_id, undefined[1], known) if undefined else f"PDBe could not provide structure [{pdb_id}]: {error}"
            # the preferred assembly, the one OPM has; unknown when the lookup failed, then the full structure was downloaded
            opm = assembly_id is not None and known is not None and assembly_id == known.preferred
    except httpx2.HTTPError:
        failed = "The structure could not be downloaded, PDBe (www.ebi.ac.uk) is not available at the moment. Please, try again later."
    except Exception as e:  # noqa: BLE001 (the session must not stay loading)
        failed = f"Structure [{pdb_id}] could not be downloaded: {e}"
    finally:
        temp.unlink(missing_ok=True)
    if failed:
        return NotDownloaded(failed, assembly_id)
    return Downloaded(file.name, assembly_id, opm)


async def fetch(http: httpx2.AsyncClient, url: str, target) -> int:
    """The HTTP status; target is written only for 200. 413 when the structure has more than MAX_STRUCTURE_MB, the
    download then stops."""
    async with http.stream("GET", url) as response:
        if response.status_code != 200:
            return response.status_code
        with target.open("wb") as out:
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_STRUCTURE_MB * MB:
                    return 413
                out.write(chunk)
    return 200


# e.g. _model_server_error.message    "Error: Assembly '3' is not defined."
SERVER_ERROR = "_model_server_error.message"
UNDEFINED_ASSEMBLY = re.compile(r"^Assembly '(.*)' is not defined\.$")


def server_error(path) -> str | None:
    """The model server answers some errors (an assembly the entry does not have) with 200 and a file stating
    the error instead of the structure"""
    with open(path, errors="replace") as structure:
        for line in structure:
            if line.startswith(SERVER_ERROR):
                return line.removeprefix(SERVER_ERROR).strip()[1:-1].removeprefix("Error: ")
    return None
