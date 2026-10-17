"""/Kill and /Delete of loads, with a fake MOLE that runs until the test lets it go"""

import time

import pytest

from .conftest import STRUCTURE, running, upload, wait, wait_for_file
from .test_runner import UNTIL_GO

STOPPED = {"Status": "FailedInitialization", "ErrorMsg": "Loading of the structure was stopped."}


def started_load(client) -> str:
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    wait_for_file(client.app.state.settings.working_directory / id / "0" / "started")
    return id


def gone(pid: int) -> bool:
    for _ in range(100):
        if not running(pid):
            return True
        time.sleep(0.02)
    return False


CHILD = 'sleep 30 & echo $! > "$(dirname "$1")/child"; '


def test_stopped_load(api):
    client = api(load=CHILD + UNTIL_GO)
    id = started_load(client)
    child = int((client.app.state.settings.working_directory / id / "0" / "child").read_text())

    response = client.post(f"/Kill/{id}")
    assert response.status_code == 200
    assert response.json() == {"ComputationId": id, "SubmitId": 1, **STOPPED}
    assert gone(child)  # the whole process tree
    time.sleep(0.2)
    assert client.get(f"/Status/{id}").json() == {"ComputationId": id, "SubmitId": 1, **STOPPED}  # the run did not write over it

    again = client.post(f"/Kill/{id}")  # nothing runs now
    assert again.status_code == 409
    assert again.json() == {"ComputationId": id, "SubmitId": 1, "Status": "Error", "ErrorMsg": 'Cannot kill computation with the status "FailedInitialization"'}


def test_load_stopped_while_waiting_for_a_slot(api):
    client = api(load=UNTIL_GO, slots=1)
    folder = client.app.state.settings.working_directory
    first = started_load(client)
    waiting = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    assert client.post(f"/Kill/{waiting}").json()["ErrorMsg"] == STOPPED["ErrorMsg"]

    (folder / first / "0" / "go").touch()
    assert wait(client, first)["Status"] == "Initialized"
    time.sleep(0.3)  # the slot is free, the stopped load must not start
    assert not (folder / waiting / "0" / "started").exists()
    assert client.get(f"/Status/{waiting}").json()["Status"] == "FailedInitialization"


def test_finished_load_cannot_be_stopped(client):
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    wait(client, id)
    response = client.post(f"/Kill/{id}")
    assert response.status_code == 409
    assert response.json()["ErrorMsg"] == 'Cannot kill computation with the status "Initialized"'


def test_delete_stops_the_run_and_is_410_after(api):
    client = api(load=CHILD + UNTIL_GO)
    id = started_load(client)
    child = int((client.app.state.settings.working_directory / id / "0" / "child").read_text())

    response = client.delete(f"/Delete/{id}")
    assert response.status_code == 200
    assert response.json() == {"ComputationId": id, "SubmitId": 1, "Status": "Deleted", "ErrorMsg": ""}
    assert gone(child)
    assert (client.app.state.settings.working_directory / id / "1tqn.cif").exists()  # the files are kept
    for response in (client.get(f"/Status/{id}"), client.post(f"/Kill/{id}"), client.delete(f"/Delete/{id}")):
        assert response.status_code == 410
        assert response.json()["ErrorMsg"] == "The computation has been deleted."


def test_delete_of_a_loaded_computation(client):
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    wait(client, id)
    assert client.delete(f"/Delete/{id}").json()["Status"] == "Deleted"
    assert client.get(f"/Status/{id}").status_code == 410


@pytest.mark.parametrize("method, path", [("POST", "/Kill/nope"), ("DELETE", "/Delete/nope")])
def test_unknown_computation(client, method, path):
    response = client.request(method, path)
    assert response.status_code == 404
    assert response.json()["ErrorMsg"] == "ComputationId [nope] does not exists.\n"
