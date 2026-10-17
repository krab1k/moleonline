import asyncio
import os
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from mole_api.config import Settings
from mole_api.versions import ToolError, read_versions

from .conftest import MOLE_VERSION, PORES_VERSION, script

MOLE = "echo 'WebChemistry Tunnels 2.5.1, (c) test'"


def test_documentation_and_version(client):
    assert "/Version" in client.get("/openapi.json").json()["paths"]
    assert client.get("/docs").status_code == 200
    response = client.get("/Version")
    assert response.status_code == 200
    assert response.json()["APIVersion"]
    assert response.json()["MoleVersion"] == MOLE_VERSION
    assert response.json()["PoresVersion"] == PORES_VERSION


def test_wrong_settings_stop_the_api(tmp_path: Path):
    (tmp_path / "file").touch()
    with pytest.raises(ValidationError) as error:
        Settings(working_directory=tmp_path / "file", max_concurrent_computations=0,
                 max_computation_minutes=0, mole="mole2", pores=tmp_path / "file")
    problems = {e["loc"][0]: e["msg"] for e in error.value.errors()}  # all of them at once
    assert "is not writable" in problems["working_directory"]
    assert "max_concurrent_computations" in problems and "max_computation_minutes" in problems
    assert "must be a full path" in problems["mole"]
    assert "is not executable" in problems["pores"]


def tools(directory: Path, mole: str, pores: str) -> Settings:
    return Settings(working_directory=directory, mole=script(directory, "mole", mole), pores=script(directory, "pores", pores))


@pytest.mark.parametrize("mole, pores, setting, problem", [
    ("echo 'not MOLE'", "echo 'pores 1.0'", "MOLE", "its first line is \"not MOLE\""),
    (MOLE, "echo 'not pores'", "PORES", "its first line is \"not pores\""),
    (MOLE, "echo 'pores 1.0'; exit 1", "PORES", "it exited with code 1"),  # a version line does not make a failing tool work
    (MOLE, "echo 'pores 1.0'; sleep 30", "PORES", "it did not finish within 1 s"),
])
def test_tool_without_a_version_stops_the_api(tmp_path: Path, mole, pores, setting, problem):
    with pytest.raises(ToolError) as error:
        asyncio.run(read_versions(tools(tmp_path, mole, pores), timeout=1))
    assert f"{setting} '" in str(error.value)
    assert f"did not report its version, {problem}." in str(error.value)


def test_stopping_the_api_while_a_tool_reports_its_version(tmp_path: Path):
    child = tmp_path / "child"
    settings = tools(tmp_path, MOLE, f"sleep 30 & echo $! > '{child}'; wait")

    async def stop():
        reading = asyncio.create_task(read_versions(settings))
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
