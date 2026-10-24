import asyncio
import contextlib
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from moleonline.api.main import create_app
from moleonline.core import storage
from moleonline.core.config import Settings
from moleonline.worker import runner, work

# a PostgreSQL database the tests empty before each test, postgresql+asyncpg://… (README.md#development)
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
MOLE_VERSION = "9.8.7.6"  # what the fake MOLE reports when started without arguments, as MOLE does
STRUCTURE = (Path(__file__).parent / "data" / "1tqn.cif").read_bytes()


def script(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o700)
    return path


async def execute(*statements: str) -> list:
    """Runs the statements in one transaction of the test database; the rows of the last one"""
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as c:
            for statement in statements:
                result = await c.execute(text(statement))
            return result.all() if result.returns_rows else []
    finally:
        await engine.dispose()


def query(*statements: str) -> list:
    return asyncio.run(execute(*statements))


@pytest.fixture
def database() -> str:
    """The test database, emptied: without the tables and Alembic's record of them, which the API makes when it starts"""
    if not TEST_DATABASE_URL:
        pytest.fail("Set TEST_DATABASE_URL to a PostgreSQL database the tests may empty, see README.md#development")
    query(*[f"DROP TABLE IF EXISTS {table} CASCADE" for table in [*storage.metadata.tables, "alembic_version"]])
    return TEST_DATABASE_URL


def make_settings(directory: Path, database: str, load: str = "exit 0", run: str = "exit 0", slots: int = 4, queued: int = 20,
                  mole: Path | None = None) -> Settings:
    """The API's own working directory. The fake MOLE runs load for a load (input in folder load/), run for a
    computation, and reports its version without arguments. mole: a real MOLE instead."""
    fake = (f"case \"$1\" in\n  '') echo 'WebChemistry Tunnels {MOLE_VERSION}, (c) test' ;;\n"
            f"  */load/input.xml) {load} ;;\n  *) {run} ;;\nesac")
    return Settings(
        working_directory=directory / "computations",
        database_url=database,
        max_concurrent_computations=slots,
        max_queued_computations=queued,
        mole=mole or script(directory, "mole", fake),
        memembed=script(directory, "memembed", "exit 1"),  # the fake pores tool does not run it
        opm_cache=directory / "opm-cache",
    )


@pytest.fixture
def settings(tmp_path: Path, database: str) -> Settings:
    return make_settings(tmp_path, database)


@pytest.fixture
def api(tmp_path: Path, database: str, monkeypatch):
    """api(load=..., run=..., pores=..., slots=...): a started API (MOLE's version is read) with these fake MOLE
    scripts and a fake pores tool running pores (input: pores.json); its runs are killed when the test ends"""
    monkeypatch.setattr(runner, "IDLE_SECONDS", 0.02)  # the slots look for runs, the tests do not wait for them
    monkeypatch.setattr(runner, "CHECK_SECONDS", 0.02)

    def start(pores: str = "exit 0", **fake) -> TestClient:
        monkeypatch.setattr(work, "PORES_TOOL", [str(script(tmp_path, "pores", pores))])
        return started.enter_context(TestClient(create_app(make_settings(tmp_path, database, **fake))))

    with contextlib.ExitStack() as started:
        yield start


@pytest.fixture
def client(api):
    return api()


def upload(client: TestClient, name: str, content: bytes):
    return client.post("/sessions/upload", files={"file": (name, content)})


def wait_session(client: TestClient, id: str, seconds: float = 30) -> dict:
    """The session once its load ended: ready or failed"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        session = client.get(f"/sessions/{id}").json()
        if session["state"] not in ("queued", "loading"):
            return session
        time.sleep(0.05)
    raise TimeoutError(f"session {id} is still loading after {seconds} s")


def wait(client: TestClient, id: str, number: int, seconds: float = 30) -> dict:
    """The job once it is no longer queued or running"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        job = client.get(f"/sessions/{id}/jobs/{number}").json()
        if job["state"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise TimeoutError(f"job {number} of session {id} is still in progress after {seconds} s")


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
    except (FileNotFoundError, ProcessLookupError):  # gone, also while being read
        return False


def gone(pid: int, seconds: float = 2) -> bool:
    """The process ends within seconds: a killed process does not end at once"""
    end = time.monotonic() + seconds
    while running(pid):
        if time.monotonic() > end:
            return False
        time.sleep(0.02)
    return True
