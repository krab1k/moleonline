"""POST /sessions: the structure from PDBe, here a fake one; nothing is sent to PDBe"""

import asyncio
import inspect
import time

import httpx2
import pytest

from .conftest import STRUCTURE, query, wait_session
from .test_requests import logged

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


def saved(id: str) -> tuple:
    """pdb_id, structure_file, assembly_id, opm of the stored session"""
    return tuple(query(f"SELECT pdb_id, structure_file, assembly_id, opm FROM sessions WHERE id = '{id}'")[0])


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
    assert saved(id) == ("1tqn", "1tqn.cif", assembly_id, pores)
    folder = client.app.state.settings.working_directory / id
    assert (folder / "1tqn.cif").read_bytes() == STRUCTURE
    assert (client.app.state.settings.scratch_directory / id / "load.xml").exists()  # MOLE loaded it


def server_error(message: str) -> bytes:
    """What the model server answers with 200 instead of a structure"""
    return f'data_1TQN\n#\n_model_server_error.message    "Error: {message}" \n#\n'.encode()


UNAVAILABLE = "The structure could not be downloaded, PDBe (www.ebi.ac.uk) is not available at the moment. Please, try again later."


@pytest.mark.parametrize("answer, message", [
    (404, "Structure [1tqn] is unlikely to exist or has been made obsolete."),
    (503, "PDBe returned an error (503) when downloading structure [1tqn]. Please, try again later."),
    (bytes(30 * 1024 * 1024 + 1), "Structure [1tqn] is larger than 30 MB, at most 30 MB can be computed."),
    (httpx2.ConnectError("refused"), UNAVAILABLE),
    (httpx2.ReadTimeout("slow"), UNAVAILABLE),
    (server_error("Something went wrong."), "PDBe could not provide structure [1tqn]: Something went wrong."),
], ids=["404", "503", "too large", "unavailable", "timeout", "server error"])
def test_failed_download(client, answer, message):
    FakePDBe(client, {FULL: answer})
    id = create(client, assembly="asymmetric").json()["id"]
    session = wait_session(client, id)
    assert session["state"] == "failed"
    assert session["error"] == message
    assert not (client.app.state.settings.scratch_directory / id / "load.xml").exists()  # MOLE did not run
    assert not list((client.app.state.settings.working_directory / id).glob("*.download"))


def test_assembly_the_entry_does_not_have(client):
    pdbe = FakePDBe(client, {XML: ASSEMBLIES})
    session = wait_session(client, create(client, assembly="3").json()["id"])
    assert session["state"] == "failed"
    assert session["error"] == "Structure [1tqn] has no assembly [3]. Its assemblies are 1, 2."
    assert pdbe.requests == [XML]  # the structure is not asked for


def test_assembly_the_entry_does_not_have_when_its_assemblies_are_unknown(client):
    # the model server says so in the file it answers with
    FakePDBe(client, {XML: 500, assembly("3"): server_error("Assembly '3' is not defined.")})
    session = wait_session(client, create(client, assembly="3").json()["id"])
    assert session["state"] == "failed"
    assert session["error"] == "Structure [1tqn] has no assembly [3]."
    assert not (client.app.state.settings.scratch_directory / session["id"] / "load.xml").exists()  # MOLE did not run


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
    assert response.status_code == 422
    assert response.json()["detail"].startswith(message)
    assert pdbe.requests == []
    assert logged(settings) == [("Init", "testclient", "PDB", "-")]


def test_loads_are_logged(client, settings):
    FakePDBe(client, {FULL: STRUCTURE})
    id = create(client, assembly="asymmetric").json()["id"]
    wait_session(client, id)
    assert logged(settings) == [("Init", "testclient", "PDB", id)]
