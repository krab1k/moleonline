"""Loads run by the process runner, with a fake MOLE: slots, the time limit, failures, stopping the API"""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from moleonline import runner, storage
from moleonline.main import create_app
from moleonline.storage import INTERRUPTED

from .conftest import STRUCTURE, gone, query, upload, wait_for_file, wait_session

# the fake MOLE marks that it started (next to its input), then waits until the test lets it go
STARTED = 'touch "$(dirname "$1")/started"'
UNTIL_GO = STARTED + '; while [ ! -f "$(dirname "$1")/go" ]; do sleep 0.02; done'


def load(client) -> str:
    return upload(client, "1tqn.cif", STRUCTURE).json()["id"]


def test_waiting_for_a_slot(api):
    client = api(load=UNTIL_GO, slots=1)
    folder = client.app.state.settings.working_directory
    first, second = load(client), load(client)
    wait_for_file(folder / first / "load" / "started")
    time.sleep(0.3)
    assert client.get(f"/sessions/{second}").json()["state"] == "queued"  # waits for the slot
    assert client.get(f"/sessions/{first}").json()["state"] == "loading"

    (folder / first / "load" / "go").touch()
    assert wait_session(client, first)["state"] == "ready"
    wait_for_file(folder / second / "load" / "started")
    assert client.get(f"/sessions/{second}").json()["state"] == "loading"
    (folder / second / "load" / "go").touch()
    assert wait_session(client, second)["state"] == "ready"


@pytest.mark.parametrize("fake, message", [
    ("exit 3", "The computation failed with exit code 3."),
    ("kill -9 $$", "The computation was terminated by the system, most likely it ran out of memory."),  # as the OOM killer
    ("echo 'Invalid structure' >&2", "Invalid structure\n"),  # MOLE reports errors on stderr, with exit code 0
])
def test_failed_load(api, fake, message):
    client = api(load=fake)
    session = wait_session(client, load(client))
    assert session["state"] == "failed"
    assert session["error"] == message


def test_tool_that_cannot_start(api):
    client = api()
    client.app.state.settings.mole.unlink()  # e.g. removed after the start
    session = wait_session(client, load(client))
    assert session["state"] == "failed"
    assert "No such file" in session["error"]


def test_time_limit_kills_the_whole_process_tree(api):
    # the pores tool starts MEMEMBED as a child; the child must not keep running
    client = api(load='sleep 30 & echo $! > "$(dirname "$1")/child"; ' + UNTIL_GO)
    client.app.state.runner.time_limit = 0.5
    id = load(client)
    session = wait_session(client, id)
    assert session["state"] == "failed"
    assert session["error"] == "The computation exceeded the time limit of 30 minutes."
    child = int((client.app.state.settings.working_directory / id / "load" / "child").read_text())
    assert gone(child)


def test_stopping_the_api_kills_runs(api, tmp_path):
    client = api(load='sleep 30 & echo $! > "$(dirname "$1")/child"; ' + UNTIL_GO)
    folder = client.app.state.settings.working_directory
    id = load(client)
    wait_for_file(folder / id / "load" / "started")
    child = int((folder / id / "load" / "child").read_text())
    client.__exit__(None, None, None)  # the API stops
    assert gone(child)
    assert query(f"SELECT state FROM sessions WHERE id = '{id}'") == [("loading",)]  # marked by the next API


def test_a_run_whose_state_cannot_be_kept_stops_the_api(api, monkeypatch):
    """The database fails while the run's outcome is stored: the API stops (and is restarted), never keeping a run it
    no longer runs; the next API marks it interrupted"""
    stopped = threading.Event()
    monkeypatch.setattr(runner, "terminate", stopped.set)
    end = storage.end

    async def failing(*args, **kwargs):
        raise OSError("the database is not available")
    monkeypatch.setattr(storage, "end", failing)
    client = api()
    id = load(client)
    assert stopped.wait(10)
    assert query(f"SELECT state FROM sessions WHERE id = '{id}'") == [("loading",)]
    monkeypatch.setattr(storage, "end", end)  # the database is back
    settings = client.app.state.settings
    client.__exit__(None, None, None)
    with TestClient(create_app(settings)) as again:
        session = again.get(f"/sessions/{id}").json()
        assert (session["state"], session["error"]) == ("failed", INTERRUPTED)
