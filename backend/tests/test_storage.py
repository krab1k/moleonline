"""Sessions, jobs and runs in the database: each change of state applies only from the state it expects, the queue's
limit holds for concurrent requests, runs left running by a stopped API are interrupted"""

import asyncio
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import text

from moleonline import runner, storage
from moleonline.main import create_app
from moleonline.storage import INTERRUPTED, STOPPED_LOAD, Database, JobState, Refused, SessionState

from .conftest import wait_session

VERSION = "2.5.26.10.17"
LIMIT = 100  # of the queue, unless a test says otherwise


def run(database: str, check) -> None:
    """check(db) in one event loop, which the database's connections belong to (asyncpg)"""
    async def main():
        db = Database(database)
        await db.migrate()
        try:
            await check(db)
        finally:
            await db.close()
    asyncio.run(main())


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "computations"
    path.mkdir()
    return path


async def ready_session(db: Database, folder: Path) -> storage.StoredSession:
    """A loaded session of an upload, its load's run done"""
    structure = folder.parent / "upload"
    structure.write_text("x")
    s = await storage.create(db, folder, LIMIT, VERSION, structure=structure, structure_file="1tqn.cif")
    assert await storage.end(db, await storage.claim(db, VERSION))
    return await storage.get(db, folder, s.id)


def test_tables_are_those_of_the_revision(database):
    """The revision (migrations/versions) makes the tables storage.py describes: made anew when they change, README.md#database"""
    async def check(db: Database):
        async with db.engine.connect() as c:
            differences = await c.run_sync(lambda sync: compare_metadata(MigrationContext.configure(sync), storage.metadata))
        assert differences == []
    run(database, check)


def test_runs_are_taken_oldest_first_and_once(database, folder):
    async def check(db: Database):
        first = await storage.create(db, folder, LIMIT, VERSION, pdb_id="1tqn", assembly="preferred")
        second = await storage.create(db, folder, LIMIT, VERSION, pdb_id="2bg9", assembly="preferred")
        taken = await asyncio.gather(*[storage.claim(db, VERSION) for _ in range(4)])  # slots looking at once
        assert sorted(r.session_id for r in taken if r) == sorted([first.id, second.id])
        assert [(await storage.get(db, folder, s.id)).state for s in (first, second)] == [SessionState.loading] * 2
    run(database, check)


def test_late_outcome_never_overwrites_a_cancel_or_a_delete(database, folder):
    async def check(db: Database):
        s = await ready_session(db, folder)
        await storage.add_job(db, s, "tunnels", {}, VERSION, LIMIT)
        job_run = await storage.claim(db, VERSION)
        assert await storage.cancel_job(db, s.id, 1)
        assert not await storage.is_running(db, job_run)  # its slot stops it
        assert not await storage.end(db, job_run, None)  # the outcome comes too late
        assert (await storage.get(db, folder, s.id)).job(1).state == JobState.cancelled

        loading = await storage.create(db, folder, LIMIT, VERSION, pdb_id="1tqn", assembly="preferred")
        load_run = await storage.claim(db, VERSION)
        await storage.delete(db, loading.id)
        assert not await storage.store_structure(db, load_run, "1tqn.cif", "1", True)
        assert not await storage.end(db, load_run, None)
        deleted = await storage.get(db, folder, loading.id)
        assert deleted.deleted and (deleted.state, deleted.error, deleted.structure_file) == (SessionState.failed, STOPPED_LOAD, None)
    run(database, check)


def test_a_run_records_the_mole_that_runs_it(database, folder):
    """Queued with one MOLE, run by an API with another (queued runs wait over a restart, also an upgrade)"""
    async def check(db: Database):
        s = await ready_session(db, folder)
        await storage.add_job(db, s, "tunnels", {}, "2.5.1", LIMIT)
        loading = await storage.create(db, folder, LIMIT, "2.5.1", pdb_id="1tqn", assembly="preferred")
        await storage.claim(db, VERSION)
        await storage.claim(db, VERSION)
        assert (await storage.get(db, folder, s.id)).job(1).mole_version == VERSION
        assert (await storage.get(db, folder, loading.id)).mole_version == VERSION
    run(database, check)


def test_a_lost_candidate_keeps_no_lock(database, folder):
    """A slot whose candidate run was cancelled while it waited for its session tries the next one without the first
    session's lock: another transaction holding the next session's lock and wanting the first's never deadlocks"""
    async def check(db: Database):
        first = await storage.create(db, folder, LIMIT, VERSION, pdb_id="1tqn", assembly="preferred")
        second = await storage.create(db, folder, LIMIT, VERSION, pdb_id="2bg9", assembly="preferred")
        lock = "SELECT id FROM sessions WHERE id = :id FOR UPDATE"
        async with db.engine.connect() as one, db.engine.connect() as other:
            await one.begin()
            await one.execute(text(lock), {"id": first.id})
            claiming = asyncio.create_task(storage.claim(db, VERSION))  # waits for the first session
            await asyncio.sleep(0.3)
            await other.begin()
            await other.execute(text("SET LOCAL lock_timeout = '3s'"))
            await other.execute(text(lock), {"id": second.id})
            await one.execute(text("UPDATE runs SET phase = 'done' WHERE session_id = :id"), {"id": first.id})  # cancelled
            await one.commit()
            await asyncio.sleep(0.3)  # the slot tries the second, waits for its session
            await other.execute(text(lock), {"id": first.id})  # free: no deadlock, no timeout
            await other.commit()
        assert (await claiming).session_id == second.id
    run(database, check)


def test_one_of_two_jobs_submitted_at_once_is_refused(database, folder):
    async def check(db: Database):
        s = await ready_session(db, folder)
        results = await asyncio.gather(*[storage.add_job(db, s, "tunnels", {}, VERSION, LIMIT) for _ in range(2)],
                                       return_exceptions=True)
        assert sorted(type(r).__name__ for r in results) == ["Job", "Refused"]
        assert next(r for r in results if isinstance(r, Refused)).status == 409
    run(database, check)


def test_the_queue_limit_holds_for_requests_at_once(database, folder):
    async def check(db: Database):
        results = await asyncio.gather(*[storage.create(db, folder, 3, VERSION, pdb_id="1tqn", assembly="preferred") for _ in range(8)],
                                       return_exceptions=True)
        assert sum(isinstance(r, storage.StoredSession) for r in results) == 3
        assert all(r is storage.QUEUE_FULL for r in results if not isinstance(r, storage.StoredSession))
        assert len(list(folder.iterdir())) == 3  # the refused ones made no folder
    run(database, check)


def test_runs_of_a_stopped_api_are_interrupted_and_queued_ones_kept(settings, tmp_path, monkeypatch):
    """The API marks the runs a previous API left running when it starts; a queued one runs then"""
    ids = {}

    async def left(db: Database):
        s = await ready_session(db, settings.working_directory)
        ids["job"] = s.id
        await storage.add_job(db, s, "tunnels", {}, VERSION, LIMIT)
        ids["load"] = (await storage.create(db, settings.working_directory, LIMIT, VERSION, pdb_id="1tqn", assembly="preferred")).id
        assert (await storage.claim(db, VERSION)).session_id == ids["job"]
        assert (await storage.claim(db, VERSION)).session_id == ids["load"]  # both running, as when the API stopped
        (tmp_path / "x").write_text("x")
        ids["queued"] = (await storage.create(db, settings.working_directory, LIMIT, VERSION, structure=tmp_path / "x",
                                              structure_file="1tqn.cif")).id
    run(settings.database_url, left)
    monkeypatch.setattr(runner, "IDLE_SECONDS", 0.02)
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/sessions/{ids['load']}").json()["error"] == INTERRUPTED
        job = client.get(f"/sessions/{ids['job']}/jobs/1").json()
        assert (job["state"], job["error"]) == ("failed", INTERRUPTED)
        assert client.get(f"/sessions/{ids['job']}").json()["state"] == "ready"  # the structure is still loaded
        assert wait_session(client, ids["queued"])["state"] == "ready"


@pytest.mark.parametrize("id", ["NoSuchSession", "..x", "a.b"])
def test_unknown_session(client, id):
    response = client.get(f"/sessions/{id}")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {"type": "about:blank", "title": "Not Found", "status": 404, "detail": f"Session [{id}] does not exist."}
