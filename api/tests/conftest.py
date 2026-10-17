import contextlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mole_api.config import Settings
from mole_api.main import create_app

MOLE_VERSION = "9.8.7.6"  # what the fake MOLE reports when started without arguments, as MOLE does
PORES_VERSION = "5.4.3"  # what the fake pores tool reports for --version
CSA = Path(__file__).parent.parent / "Resources" / "mcsa_sites.csv.gz"
STRUCTURE = (Path(__file__).parent / "data" / "1tqn.cif").read_bytes()


def script(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o700)
    return path


def make_settings(directory: Path, load: str = "exit 0", run: str = "exit 0", slots: int = 4) -> Settings:
    """The API's own working directory. The fake MOLE runs load for a load (input in folder 0), run for a
    computation, and reports its version without arguments; the fake pores tool only reports its version."""
    mole = f"case \"$1\" in\n  '') echo 'WebChemistry Tunnels {MOLE_VERSION}, (c) test' ;;\n  */0/input.xml) {load} ;;\n  *) {run} ;;\nesac"
    return Settings(
        working_directory=directory / "computations",
        csa=CSA,
        max_concurrent_computations=slots,
        mole=script(directory, "mole", mole),
        pores=script(directory, "pores", f"[ \"$1\" = --version ] && echo 'pores {PORES_VERSION}' || exit 1"),
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def api(tmp_path: Path):
    """api(load=..., run=..., slots=...): a started API (the versions are read) with these fake MOLE scripts; its
    runs are killed when the test ends"""
    with contextlib.ExitStack() as started:
        yield lambda **fake: started.enter_context(TestClient(create_app(make_settings(tmp_path, **fake))))


@pytest.fixture
def client(api):
    return api()


def upload(client: TestClient, name: str, content: bytes):
    return client.post("/Init", files={"file": (name, content)})


def wait(client: TestClient, id: str, submit_id: int = 0, seconds: float = 30) -> dict:
    """The status once the load or the submission is no longer in progress"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        status = client.get(f"/Status/{id}", params={"submitId": submit_id}).json()
        if status["Status"] not in ("Initializing", "Running"):
            return status
        time.sleep(0.05)
    raise TimeoutError(f"computation {id} is still in progress after {seconds} s")


def wait_for_file(path: Path, seconds: float = 10) -> None:
    end = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > end:
            raise TimeoutError(f"{path} did not appear")
        time.sleep(0.02)


def running(pid: int) -> bool:
    """A process that exists and is not a zombie (killed, waiting to be reaped)"""
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False
