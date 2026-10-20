"""POST /sessions: the structure from PDBe, here a fake one; nothing is sent to PDBe"""

import asyncio
import inspect
import json
import time

import httpx2
import pytest

from .conftest import STRUCTURE, wait_session
from .test_iplog import logged

ASSEMBLIES = b'<assemblies><assembly id="1" prefered="True"/><assembly id="2" prefered="False"/></assemblies>'


class FakePDBe:
    """Answers by path (with the query); a value may be a status code, bytes (200), an exception or a coroutine"""

    def __init__(self, client, routes: dict):
        self.routes, self.requests = routes, []
        client.app.state.pdbe = httpx2.AsyncClient(base_url="https://www.ebi.ac.uk/pdbe", transport=httpx2.MockTransport(self.handle))

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.raw_path.decode().removeprefix("/pdbe")
        self.requests.append(path)
        answer = self.routes.get(path, 404)
        if inspect.iscoroutinefunction(answer):
            answer = await answer()
        if isinstance(answer, Exception):
            raise answer
        return httpx2.Response(answer) if isinstance(answer, int) else httpx2.Response(200, content=answer)


XML = "/static/entry/download/1tqn-assembly.xml"
FULL = "/model-server/v1/1tqn/full"


def assembly(name: str) -> str:
    return f"/model-server/v1/1tqn/assembly?name={name}"


def create(client, pdb_id: str = "1tqn", **assembly):
    return client.post("/sessions", json={"pdb_id": pdb_id, **assembly})


def saved(client, id: str) -> dict:
    return json.loads((client.app.state.settings.working_directory / id / "_object.json").read_text())


@pytest.mark.parametrize("requested, routes, file, assembly_id, pores", [
    ({}, {XML: ASSEMBLIES, assembly("1"): STRUCTURE}, assembly("1"), "1", True),  # the preferred one
    ({"assembly": "preferred"}, {XML: 500, FULL: STRUCTURE}, FULL, None, False),  # not known: the asymmetric unit
    ({"assembly": "asymmetric"}, {FULL: STRUCTURE}, FULL, None, False),
    ({"assembly": "2"}, {XML: ASSEMBLIES, assembly("2"): STRUCTURE}, assembly("2"), "2", False),
    ({"assembly": "1"}, {XML: ASSEMBLIES, assembly("1"): STRUCTURE}, assembly("1"), "1", True),  # the preferred one by its id
])
def test_structure_is_downloaded_and_loaded(client, requested, routes, file, assembly_id, pores):
    pdbe = FakePDBe(client, routes)
    response = create(client, "1TQN", **requested)
    assert response.status_code == 202
    id = response.json()["id"]
    assert response.headers["location"] == f"/sessions/{id}"
    assert response.json()["state"] == "queued" and response.json()["pdb_id"] == "1tqn"
    session = wait_session(client, id)
    assert session["state"] == "ready" and session["assembly_id"] == assembly_id

    assert file in pdbe.requests
    status = saved(client, id)
    assert (status["PdbId"], status["StructureFile"], status["AssemblyId"], status["DbModePores"]) == ("1tqn", "1tqn.cif", assembly_id, pores)
    assert not status["UserStructure"]
    folder = client.app.state.settings.working_directory / id
    assert (folder / "1tqn.cif").read_bytes() == STRUCTURE
    assert (folder / "0" / "input.xml").exists()


ERROR_CIF = b"data_error\n_coordinate_server_error.message 'Entry not found.'\n"


@pytest.mark.parametrize("answer, message", [
    (404, "Structure [1tqn] is unlikely to exist or has been made obsolete."),
    (503, "PDBe returned an error (503) when downloading structure [1tqn]. Please, try again later."),
    (bytes(30 * 1024 * 1024 + 1), "Structure [1tqn] is larger than 30 MB, at most 30 MB can be computed."),
    (httpx2.ConnectError("refused"), "The structure could not be downloaded, PDBe (www.ebi.ac.uk) is not available at the moment. Please, try again later."),
    (httpx2.ReadTimeout("slow"), "The structure could not be downloaded, PDBe (www.ebi.ac.uk) is not available at the moment. Please, try again later."),
    (ERROR_CIF, "Entry not found."),
], ids=["404", "503", "too large", "unavailable", "timeout", "server error"])
def test_failed_download(client, answer, message):
    FakePDBe(client, {FULL: answer})
    id = create(client, assembly="asymmetric").json()["id"]
    session = wait_session(client, id)
    assert session["state"] == "failed"
    assert session["error"] == message
    assert not (client.app.state.settings.working_directory / id / "0").exists()  # MOLE did not run
    assert not list((client.app.state.settings.working_directory / id).glob("*.download"))


def test_stopped_download(client):
    started = asyncio.Event()

    async def forever():
        started.set()
        await asyncio.Event().wait()

    FakePDBe(client, {FULL: forever})
    id = create(client, assembly="asymmetric").json()["id"]
    for _ in range(100):  # PDBe was asked
        if started.is_set():
            break
        time.sleep(0.01)
    assert started.is_set()
    response = client.post(f"/sessions/{id}/cancel")
    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert response.json()["error"] == "Loading of the structure was stopped."
    assert client.post(f"/sessions/{id}/cancel").status_code == 409  # nothing runs now


@pytest.mark.parametrize("requested, message", [
    ({"pdb_id": "1tqn", "assembly": "a-b"}, "Invalid assembly [a-b]."),
    ({"pdb_id": "1tqn", "assembly": "12345678901"}, "Invalid assembly [12345678901]."),
    ({"pdb_id": "1tq.n"}, "Invalid PDB id [1tq.n]."),
])
def test_refused_before_pdbe_is_asked(client, settings, requested, message):
    pdbe = FakePDBe(client, {})
    response = client.post("/sessions", json=requested)
    assert response.status_code == 400
    assert response.json()["ComputationId"] is None
    assert response.json()["ErrorMsg"].startswith(message)
    assert pdbe.requests == []
    assert logged(settings) == [("Init", "testclient", "PDB", "-")]


def test_loads_are_logged(client, settings):
    FakePDBe(client, {FULL: STRUCTURE})
    id = create(client, assembly="asymmetric").json()["id"]
    wait_session(client, id)
    assert logged(settings) == [("Init", "testclient", "PDB", id)]
