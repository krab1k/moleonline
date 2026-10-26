"""Loads run by the process runner, with a fake MOLE: slots, the time limit, failures, stopping the API"""

import asyncio
import time

import pytest

from moleonline.core import storage
from moleonline.core.storage import INTERRUPTED

from .conftest import STRUCTURE, gone, upload, wait_for_file, wait_session

# the fake MOLE marks that it started (in the run's folder), then waits until the test lets it go
STARTED = 'touch "$(dirname "$1")/started"'
UNTIL_GO = STARTED + '; while [ ! -f "$(dirname "$1")/go" ]; do sleep 0.02; done'


def load(client) -> str:
    return upload(client, "1tqn.cif", STRUCTURE).json()["id"]


def test_waiting_for_a_slot(api):
    client = api(load=UNTIL_GO, workers=1)
    folder = client.app.state.settings.scratch_directory
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
    client = api(load='sleep 30 & echo $! > "$(dirname "$1").child"; ' + UNTIL_GO)  # outlives the run's folder
    for worker in client.workers.workers:
        worker.time_limit = 0.5
    id = load(client)
    session = wait_session(client, id)
    assert session["state"] == "failed"
    assert session["error"] == "The computation exceeded the time limit of 30 minutes."
    child = int((client.app.state.settings.scratch_directory / id / "load.child").read_text())
    assert gone(child)


def expire_all(settings) -> int:
    """The API's cleanup, as if the leases' timeout had passed"""
    async def expire() -> int:
        db = storage.Database(settings.database_url)
        try:
            return await storage.expire(db, 0)
        finally:
            await db.close()
    return asyncio.run(expire())


def test_stopping_a_worker_kills_its_run(api):
    """SIGTERM (a deploy): the run's processes are killed with their children; its lease expires and it fails as
    interrupted"""
    client = api(load='sleep 30 & echo $! > "$(dirname "$1")/child"; ' + UNTIL_GO, workers=1)
    folder = client.app.state.settings.scratch_directory
    id = load(client)
    wait_for_file(folder / id / "load" / "started")
    child = int((folder / id / "load" / "child").read_text())
    client.workers.stop()
    assert gone(child)
    assert client.workers.errors == []  # stopped, not failed
    assert client.get(f"/sessions/{id}").json()["state"] == "loading"  # until its lease expires
    assert expire_all(client.app.state.settings) == 1
    session = client.get(f"/sessions/{id}").json()
    assert (session["state"], session["error"]) == ("failed", INTERRUPTED)


def test_a_worker_that_cannot_store_an_outcome_stops(api, monkeypatch):
    """The database fails while a run's outcome is stored: the worker kills the run and stops (and is restarted), never
    keeping a run it no longer runs; the API goes on, the run's lease expires"""
    async def failing(*args, **kwargs):
        raise OSError("the database is not available")
    monkeypatch.setattr(storage, "end", failing)
    client = api(workers=1)
    id = load(client)
    end = time.monotonic() + 10
    while client.workers.alive() and time.monotonic() < end:
        time.sleep(0.02)
    assert client.workers.alive() == 0
    assert [str(e) for e in client.workers.errors] == ["the database is not available"]
    assert client.get(f"/sessions/{id}").json()["state"] == "loading"
    assert expire_all(client.app.state.settings) == 1
    assert client.get(f"/sessions/{id}").json()["error"] == INTERRUPTED


def test_a_cancel_does_not_stop_the_worker(api):
    """The worker kills the cancelled run, waits for its threads and takes the next run"""
    client = api(load=UNTIL_GO, workers=1)
    folder = client.app.state.settings.scratch_directory
    first = load(client)
    wait_for_file(folder / first / "load" / "started")
    assert client.post(f"/sessions/{first}/cancel").status_code == 200
    second = load(client)
    wait_for_file(folder / second / "load" / "started")  # the same worker took it
    assert not (folder / first / "load").exists()  # its folder removed before
    (folder / second / "load" / "go").touch()
    assert wait_session(client, second)["state"] == "ready"
    assert client.workers.alive() == 1 and client.workers.errors == []
