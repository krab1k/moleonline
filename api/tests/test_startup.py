import asyncio
import os
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from mole_api.config import Settings
from mole_api.versions import ToolError, read_mole_version

from .conftest import MOLE_VERSION, script

MOLE = "echo 'WebChemistry Tunnels 2.5.1, (c) test'"


def test_documentation_and_version(client):
    assert "/Version" in client.get("/openapi.json").json()["paths"]
    assert client.get("/docs").status_code == 200
    response = client.get("/Version")
    assert response.status_code == 200
    assert response.json()["APIVersion"]
    assert response.json()["MoleVersion"] == MOLE_VERSION


def test_wrong_settings_stop_the_api(tmp_path: Path):
    (tmp_path / "file").touch()
    with pytest.raises(ValidationError) as error:
        Settings(working_directory=tmp_path / "file", max_concurrent_computations=0,
                 max_computation_minutes=0, mole=tmp_path / "file")
    problems = {e["loc"][0]: e["msg"] for e in error.value.errors()}  # all of them at once
    assert "is not writable" in problems["working_directory"]
    assert "max_concurrent_computations" in problems and "max_computation_minutes" in problems
    assert "is not executable" in problems["mole"]
    with pytest.raises(ValidationError, match="must be a full path"):
        Settings(working_directory=tmp_path, mole="mole2")


def tools(directory: Path, mole: str) -> Settings:
    return Settings(working_directory=directory, mole=script(directory, "mole", mole))


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
