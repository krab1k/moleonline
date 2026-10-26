import asyncio
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from moleonline.api.main import create_app
from moleonline.core.config import ApiSettings, WorkerSettings
from moleonline.worker.versions import ToolError, read_mole_version

from .conftest import MOLE_VERSION, STRUCTURE, query, script, upload, wait_session

MOLE = "echo 'WebChemistry Tunnels 2.5.1, (c) test'"


def test_documentation_version_and_cofactors(client):
    assert "/version" in client.get("/openapi.json").json()["paths"]
    assert client.get("/docs").status_code == 200
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json()["api"]
    assert response.json()["mole"] is None  # no worker took a run yet
    wait_session(client, upload(client, "1tqn.cif", STRUCTURE).json()["id"])
    assert client.get("/version").json()["mole"] == MOLE_VERSION  # of the worker that took it
    cofactors = client.get("/cofactors").json()
    assert cofactors and all(isinstance(name, str) and isinstance(query, str) for name, query in cofactors.items())


def test_wrong_settings_stop_the_api_and_the_workers(tmp_path: Path):
    (tmp_path / "file").touch()
    with pytest.raises(ValidationError) as error:
        WorkerSettings(working_directory=tmp_path / "file", database_url="postgresql+asyncpg://", worker_timeout_seconds=1,
                       max_computation_minutes=0, mole=tmp_path / "file", memembed=tmp_path / "missing", opm_cache=tmp_path / "file")
    problems = {e["loc"][0]: e["msg"] for e in error.value.errors()}  # all of them at once
    assert "is not writable" in problems["working_directory"]
    assert "worker_timeout_seconds" in problems and "max_computation_minutes" in problems
    assert "is not executable" in problems["mole"]
    assert "does not exist" in problems["memembed"]
    assert "is not writable" in problems["opm_cache"]
    with pytest.raises(ValidationError, match="must be a full path"):
        WorkerSettings(working_directory=tmp_path, database_url="postgresql+asyncpg://", mole="mole2")
    with pytest.raises(ValidationError) as error:
        ApiSettings(working_directory=tmp_path, max_outstanding_computations=0)
    assert {e["loc"][0] for e in error.value.errors()} == {"database_url", "max_outstanding_computations"}


def tools(directory: Path, mole: str) -> WorkerSettings:
    return WorkerSettings(working_directory=directory, database_url="postgresql+asyncpg://", mole=script(directory, "mole", mole),
                    memembed=script(directory, "memembed", "exit 0"),
                    opm_cache=directory / "opm-cache")


@pytest.mark.parametrize("mole, problem", [
    ("echo 'not MOLE'", "its first line is \"not MOLE\""),
    (f"{MOLE}; exit 1", "it exited with code 1"),  # a version line does not make a failing tool work
    (f"{MOLE}; sleep 30", "it did not finish within 1 s"),
])
def test_mole_without_a_version_stops_the_api(tmp_path: Path, mole, problem):
    with pytest.raises(ToolError) as error:
        asyncio.run(read_mole_version(tools(tmp_path, mole), timeout=1))
    assert "MOLE '" in str(error.value)
    assert f"did not report its version, {problem}." in str(error.value)


def test_stopping_the_api_while_a_tool_reports_its_version(tmp_path: Path):
    child = tmp_path / "child"
    settings = tools(tmp_path, f"sleep 30 & echo $! > '{child}'; wait")

    async def stop():
        reading = asyncio.create_task(read_mole_version(settings))
        await asyncio.sleep(0.3)
        reading.cancel()
        with pytest.raises(asyncio.CancelledError):  # not reported as a wrong setting
            await reading

    asyncio.run(stop())
    pid = int(child.read_text())
    for _ in range(50):  # the tool's child is killed with it
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    pytest.fail(f"the tool's child {pid} still runs")


def test_health_needs_no_database(settings):
    """The liveness probe's endpoint answers also while the database is away, and is not part of the API's description"""
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        query("DROP TABLE runs CASCADE")  # what /version reads is not there, as when the database is away
        assert client.get("/version").status_code == 500
        assert client.get("/health").json() == {"status": "ok"}
        assert "/health" not in client.get("/openapi.json").json()["paths"]
