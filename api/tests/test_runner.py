"""Loads run by the process runner, with a fake MOLE: slots, the time limit, failures, stopping the API"""

import json
import time

import pytest

from .conftest import STRUCTURE, running, upload, wait, wait_for_file

# the fake MOLE marks that it started (next to its input), then waits until the test lets it go
STARTED = 'touch "$(dirname "$1")/started"'
UNTIL_GO = STARTED + '; while [ ! -f "$(dirname "$1")/go" ]; do sleep 0.02; done'


def load(client) -> str:
    return upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]


def test_waiting_for_a_slot(api):
    client = api(load=UNTIL_GO, slots=1)
    folder = client.app.state.settings.working_directory
    first, second = load(client), load(client)
    wait_for_file(folder / first / "0" / "started")
    time.sleep(0.3)
    saved = lambda id: json.loads((folder / id / "_object.json").read_text())["ComputationUnits"][0]
    assert saved(second)["Started"] is None  # waits for the slot
    assert client.get(f"/Status/{second}").json()["Status"] == "Initializing"

    (folder / first / "0" / "go").touch()
    assert wait(client, first)["Status"] == "Initialized"
    wait_for_file(folder / second / "0" / "started")
    assert saved(second)["Started"] > saved(second)["Submitted"]
    (folder / second / "0" / "go").touch()
    assert wait(client, second)["Status"] == "Initialized"


@pytest.mark.parametrize("fake, message", [
    ("exit 3", "The computation failed with exit code 3."),
    ("kill -9 $$", "The computation was terminated by the system, most likely it ran out of memory."),  # as the OOM killer
    ("echo 'Invalid structure' >&2", "Invalid structure\n"),  # MOLE reports errors on stderr, with exit code 0
])
def test_failed_load(api, fake, message):
    client = api(load=fake)
    status = wait(client, load(client))
    assert status["Status"] == "FailedInitialization"
    assert status["ErrorMsg"] == message


def test_tool_that_cannot_start(api):
    client = api()
    client.app.state.settings.mole.unlink()  # e.g. removed after the start
    status = wait(client, load(client))
    assert status["Status"] == "FailedInitialization"
    assert "No such file" in status["ErrorMsg"]


def test_time_limit_kills_the_whole_process_tree(api):
    # the pores tool starts MEMEMBED as a child; the child must not keep running
    client = api(load='sleep 30 & echo $! > "$(dirname "$1")/child"; ' + UNTIL_GO)
    client.app.state.runner.time_limit = 0.5
    id = load(client)
    status = wait(client, id)
    assert status["Status"] == "FailedInitialization"
    assert status["ErrorMsg"] == "The computation exceeded the time limit of 30 minutes."
    child = int((client.app.state.settings.working_directory / id / "0" / "child").read_text())
    for _ in range(100):
        if not running(child):
            break
        time.sleep(0.02)
    assert not running(child)


def test_stopping_the_api_kills_runs(api, tmp_path):
    client = api(load='sleep 30 & echo $! > "$(dirname "$1")/child"; ' + UNTIL_GO)
    folder = client.app.state.settings.working_directory
    id = load(client)
    wait_for_file(folder / id / "0" / "started")
    child = int((folder / id / "0" / "child").read_text())
    client.__exit__(None, None, None)  # the API stops
    assert not running(child)
    assert json.loads((folder / id / "_object.json").read_text())["ComputationUnits"][0]["Status"] == "Initializing"  # marked by the next API
