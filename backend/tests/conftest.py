import asyncio
import contextlib
import os
import threading
import time
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from moleonline.api.main import create_app
from moleonline.core import storage
from moleonline.core.config import ApiSettings, WorkerSettings
from moleonline.worker import runner, work
from moleonline.worker.runner import Worker
from moleonline.worker.versions import read_mole_version

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


class Settings(ApiSettings, WorkerSettings):
    """Of both, the API and the workers a test starts"""


def make_settings(directory: Path, database: str, load: str = "exit 0", run: str = "exit 0", workers: int = 4, queued: int = 20,
                  mole: Path | None = None) -> Settings:
    """Their own working directory. The fake MOLE runs load for a load (input in folder load/), run for a computation,
    and reports its version without arguments. mole: a real MOLE instead. queued: runs that may wait for the workers."""
    fake = (f"case \"$1\" in\n  '') echo 'WebChemistry Tunnels {MOLE_VERSION}, (c) test' ;;\n"
            f"  */load/input.xml) {load} ;;\n  *) {run} ;;\nesac")
    return Settings(
        working_directory=directory / "computations",
        database_url=database,
        max_outstanding_computations=workers + queued,
        mole=mole or script(directory, "mole", fake),
        memembed=script(directory, "memembed", "exit 1"),  # the fake pores tool does not run it
        opm_cache=directory / "opm-cache",
        heartbeat_file=directory / "heartbeat",
    )


class Workers:
    """Workers, each in a thread of its own with its event loop and connections, as in processes of their own. http:
    PDBe's client, when it is needed (the tests replace it)."""

    def __init__(self, settings: Settings, count: int, http):
        self.settings, self.http = settings, http
        self.workers: list[Worker] = []
        self.errors: list[BaseException] = []  # why workers stopped, other than being stopped
        self.loops: list[tuple[asyncio.AbstractEventLoop, asyncio.Task]] = []
        self.threads = [threading.Thread(target=asyncio.run, args=(self._main(),), daemon=True) for _ in range(count)]
        self.lock = threading.Lock()

    async def _main(self) -> None:
        db = storage.Database(self.settings.database_url)
        # as python -m moleonline.worker: the fake MOLE reports MOLE_VERSION, a real one its own
        worker = Worker(self.settings, db, self.http, await read_mole_version(self.settings))
        with self.lock:
            self.workers.append(worker)
            self.loops.append((asyncio.get_running_loop(), asyncio.current_task()))
        try:
            await worker.run()
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            self.errors.append(e)
        finally:
            await db.close()

    def start(self) -> None:
        for thread in self.threads:
            thread.start()
        while len(self.loops) < len(self.threads):
            time.sleep(0.01)

    def stop(self) -> None:
        """As SIGTERM: the running runs' processes are killed"""
        for loop, task in self.loops:
            with contextlib.suppress(RuntimeError):  # stopped already, its loop closed
                loop.call_soon_threadsafe(task.cancel)
        for thread in self.threads:
            thread.join(10)

    def alive(self) -> int:
        return sum(thread.is_alive() for thread in self.threads)


def no_pdbe(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(503)  # the tests never ask PDBe; those of downloads replace the client (test_pdbe.py)


@pytest.fixture
def settings(tmp_path: Path, database: str) -> Settings:
    return make_settings(tmp_path, database)


@pytest.fixture
def api(tmp_path: Path, database: str, monkeypatch):
    """api(load=..., run=..., pores=..., workers=..., queued=...): a started API and its workers (client.workers) with
    these fake MOLE scripts and a fake pores tool running pores (input: pores.json); the workers are stopped, their runs
    killed, when the test ends. PDBe's client is client.app.state.pdbe (the tests replace it)."""
    monkeypatch.setattr(runner, "IDLE_SECONDS", 0.02)  # the workers look for runs, the tests do not wait for them
    monkeypatch.setattr(runner, "CHECK_SECONDS", 0.02)

    def start(pores: str = "exit 0", workers: int = 4, **fake) -> TestClient:
        monkeypatch.setattr(work, "PORES_TOOL", [str(script(tmp_path, "pores", pores))])
        settings = make_settings(tmp_path, database, workers=workers, **fake)
        client = started.enter_context(TestClient(create_app(settings)))  # the API migrates the database first
        client.app.state.pdbe = httpx2.AsyncClient(base_url="https://www.ebi.ac.uk/pdbe", transport=httpx2.MockTransport(no_pdbe))
        client.workers = Workers(settings, workers, lambda: client.app.state.pdbe)
        client.workers.start()
        started.callback(client.workers.stop)
        return client

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
