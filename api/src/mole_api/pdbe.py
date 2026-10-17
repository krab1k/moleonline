"""Structures of PDB entries from PDBe: the model server (https://www.ebi.ac.uk/pdbe/model-server) builds the asymmetric
unit or an assembly; the preferred assembly is the one OPM has (pores of a PDB entry)."""

import asyncio
import re
from xml.etree import ElementTree as ET

import httpx2

from .computation import Computation, Status
from .upload import MAX_STRUCTURE_MB, MB

PREFERRED, ASYMMETRIC = "preferred", "asymmetric"
ASSEMBLY = re.compile(r"^[A-Za-z0-9]{1,10}$")  # goes into PDBe's URL
PDB_ID = re.compile(r"^[A-Za-z0-9_]{1,12}$")  # also extended ids (pdb_00001abc); a file name and part of PDBe's URL


def client() -> httpx2.AsyncClient:
    # the model server builds assemblies on the fly: up to 120 s for the response, and between its parts
    return httpx2.AsyncClient(base_url="https://www.ebi.ac.uk/pdbe", timeout=httpx2.Timeout(120, connect=15))


async def preferred_assembly(http: httpx2.AsyncClient, pdb_id: str) -> str | None:
    """None when it cannot be looked up"""
    try:
        response = await http.get(f"/static/entry/download/{pdb_id}-assembly.xml")
        if response.status_code != 200:
            return None
        return next((a.get("id") for a in ET.fromstring(response.content).iter("assembly") if a.get("prefered") == "True"), None)
    except (httpx2.HTTPError, ET.ParseError):
        return None


async def download(http: httpx2.AsyncClient, c: Computation, assembly: str) -> bool:
    """Downloads the structure into the computation; False when it failed, the status then says why.
    assembly: PREFERRED (the asymmetric unit when it cannot be looked up), ASYMMETRIC or an assembly id."""
    pdb_id = c.PdbId
    c.AssemblyId = None if assembly == ASYMMETRIC else await preferred_assembly(http, pdb_id) if assembly == PREFERRED else assembly
    url = f"/model-server/v1/{pdb_id}/assembly?name={c.AssemblyId}" if c.AssemblyId else f"/model-server/v1/{pdb_id}/full"
    file = c.folder() / f"{pdb_id}.cif"
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
            c.StructureFile = file.name
            failed = await asyncio.to_thread(server_error, file)
            # the preferred assembly, the one OPM has; unknown when the lookup failed, then the full structure was downloaded
            c.DbModePores = c.AssemblyId is not None and (assembly == PREFERRED or c.AssemblyId == await preferred_assembly(http, pdb_id))
    except httpx2.HTTPError:
        failed = "The structure could not be downloaded, PDBe (www.ebi.ac.uk) is not available at the moment. Please, try again later."
    except Exception as e:
        failed = f"Structure [{pdb_id}] could not be downloaded: {e}"
    finally:
        temp.unlink(missing_ok=True)
    if failed:
        c.change_status(Status.FailedInitialization, failed)
        return False
    c.save()
    return True


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


def server_error(path) -> str | None:
    """The model server reports an error in the file it returns: _coordinate_server_error.message 'Entry not found.'"""
    with open(path, errors="replace") as structure:
        for line in structure:
            if line.startswith("_coordinate_server_error.message"):
                return line.removeprefix("_coordinate_server_error.message").strip()[1:-1]
    return None
