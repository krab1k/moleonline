"""Cancelling and deleting sessions, with a fake MOLE that runs until the test lets it go"""

import time

import pytest

from .conftest import STRUCTURE, running, upload, wait, wait_for_file, wait_session
from .test_runner import UNTIL_GO
from .test_submit import submit

STOPPED = "Loading of the structure was stopped."


def started_load(client) -> str:
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    wait_for_file(client.app.state.settings.working_directory / id / "0" / "started")
    return id


def gone(pid: int) -> bool:
    for _ in range(100):
        if not running(pid):
            return True
        time.sleep(0.02)
    return False


CHILD = 'sleep 30 & echo $! > "$(dirname "$1")/child"; '


def test_cancelled_load(api):
    client = api(load=CHILD + UNTIL_GO)
    id = started_load(client)
    assert client.get(f"/sessions/{id}").json()["state"] == "loading"
    child = int((client.app.state.settings.working_directory / id / "0" / "child").read_text())

    response = client.post(f"/sessions/{id}/cancel")
    assert response.status_code == 200
    assert (response.json()["state"], response.json()["error"]) == ("failed", STOPPED)
    assert gone(child)  # the whole process tree
    time.sleep(0.2)
    session = client.get(f"/sessions/{id}").json()
    assert (session["state"], session["error"]) == ("failed", STOPPED)  # the run did not write over it

    again = client.post(f"/sessions/{id}/cancel")  # nothing runs now
    assert again.status_code == 409
    assert again.json()["ErrorMsg"] == "The session is failed, only a queued or loading session can be cancelled."


def test_load_cancelled_while_waiting_for_a_slot(api):
    client = api(load=UNTIL_GO, slots=1)
    folder = client.app.state.settings.working_directory
    first = started_load(client)
    waiting = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    assert client.get(f"/sessions/{waiting}").json()["state"] == "queued"
    assert client.post(f"/sessions/{waiting}/cancel").json()["error"] == STOPPED

    (folder / first / "0" / "go").touch()
    assert wait_session(client, first)["state"] == "ready"
    time.sleep(0.3)  # the slot is free, the cancelled load must not start
    assert not (folder / waiting / "0" / "started").exists()
    assert client.get(f"/sessions/{waiting}").json()["state"] == "failed"


def test_ready_session_cannot_be_cancelled_and_its_job_runs_on(api):
    client = api(run=UNTIL_GO)
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    assert wait_session(client, id)["state"] == "ready"
    response = client.post(f"/sessions/{id}/cancel")
    assert response.status_code == 409
    assert response.json()["ErrorMsg"] == "The session is ready, only a queued or loading session can be cancelled."

    submit(client, id)
    folder = client.app.state.settings.working_directory / id / "1"
    wait_for_file(folder / "started")
    assert client.post(f"/sessions/{id}/cancel").status_code == 409  # jobs are cancelled on their own
    (folder / "go").touch()
    assert wait(client, id, 1)["state"] == "finished"


def test_delete_stops_the_run_and_is_410_after(api):
    client = api(load=CHILD + UNTIL_GO)
    id = started_load(client)
    child = int((client.app.state.settings.working_directory / id / "0" / "child").read_text())

    response = client.delete(f"/sessions/{id}")
    assert response.status_code == 204
    assert response.content == b""
    assert gone(child)
    assert (client.app.state.settings.working_directory / id / "1tqn.cif").exists()  # the files are kept
    for response in (client.get(f"/sessions/{id}"), client.post(f"/sessions/{id}/cancel"), client.delete(f"/sessions/{id}"),
                     client.get(f"/sessions/{id}/jobs/1"), client.post(f"/sessions/{id}/jobs", json={"type": "tunnels", "parameters": {}})):
        assert response.status_code == 410
        assert response.json()["ErrorMsg"] == "The computation has been deleted."


def test_delete_of_a_ready_session(client):
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    wait_session(client, id)
    assert client.delete(f"/sessions/{id}").status_code == 204
    assert client.get(f"/sessions/{id}").status_code == 410


@pytest.mark.parametrize("method, path", [("GET", "/sessions/nope"), ("POST", "/sessions/nope/cancel"), ("DELETE", "/sessions/nope")])
def test_unknown_session(client, method, path):
    response = client.request(method, path)
    assert response.status_code == 404
    assert response.json()["ErrorMsg"] == "ComputationId [nope] does not exists.\n"
