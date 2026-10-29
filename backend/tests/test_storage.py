"""Sessions, jobs and runs in the database: each change of state applies only from the state it expects, the queue's
limit holds for concurrent requests, a run whose lease expired fails as interrupted and is never written by its worker"""

import asyncio
import dataclasses
import time
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import text

from moleonline.api.main import create_app
from moleonline.core import storage
from moleonline.core.storage import INTERRUPTED, STOPPED_LOAD, Database, JobState, Refused, SessionState

VERSION = "2.5.26.10.17"
LIMIT = storage.Admission(100)  # of the queue, unless a test says otherwise
WORKER, TIMEOUT = "worker-1", 60  # holding the leases of the runs it takes; seconds until a lease not renewed expires


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
    s = await storage.create(db, folder, LIMIT, structure=structure, structure_file="1tqn.cif")
    assert await storage.end(db, await storage.claim(db, WORKER, VERSION, TIMEOUT))
    return await storage.get(db, folder, s.id)


def test_tables_are_those_of_the_revision(database):
    """The revision (migrations/versions) makes the tables storage.py describes: made anew when they change, README.md#database-schema"""
    async def check(db: Database):
        async with db.engine.connect() as c:
            differences = await c.run_sync(lambda sync: compare_metadata(MigrationContext.configure(sync), storage.metadata))
        assert differences == []
    run(database, check)


def test_runs_are_taken_oldest_first_and_once(database, folder):
    async def check(db: Database):
        first = await storage.create(db, folder, LIMIT, pdb_id="1tqn", assembly="preferred")
        second = await storage.create(db, folder, LIMIT, pdb_id="2bg9", assembly="preferred")
        taken = await asyncio.gather(*[storage.claim(db, WORKER, VERSION, TIMEOUT) for _ in range(4)])  # workers looking at once
        assert sorted(r.session_id for r in taken if r) == sorted([first.id, second.id])
        assert [(await storage.get(db, folder, s.id)).state for s in (first, second)] == [SessionState.loading] * 2
    run(database, check)


def test_late_outcome_never_overwrites_a_cancel_or_a_delete(database, folder):
    async def check(db: Database):
        s = await ready_session(db, folder)
        await storage.add_job(db, s, "tunnels", {}, LIMIT)
        job_run = await storage.claim(db, WORKER, VERSION, TIMEOUT)
        assert await storage.cancel_job(db, s.id, 1)
        assert not await storage.renew(db, job_run)  # its worker stops it
        assert not await storage.end(db, job_run, None)  # the outcome comes too late
        assert (await storage.get(db, folder, s.id)).job(1).state == JobState.cancelled

        loading = await storage.create(db, folder, LIMIT, pdb_id="1tqn", assembly="preferred")
        load_run = await storage.claim(db, WORKER, VERSION, TIMEOUT)
        await storage.delete(db, loading.id)
        assert not await storage.store_structure(db, load_run, "1tqn.cif", "1", True)
        assert not await storage.end(db, load_run, None)
        deleted = await storage.get(db, folder, loading.id)
        assert deleted.deleted and (deleted.state, deleted.error, deleted.structure_file) == (SessionState.failed, STOPPED_LOAD, None)
    run(database, check)


def test_a_run_records_the_mole_that_runs_it(database, folder):
    """Taken by a worker with its MOLE: queued runs wait over a restart, also an upgrade of the workers"""
    async def check(db: Database):
        s = await ready_session(db, folder)
        await storage.add_job(db, s, "tunnels", {}, LIMIT)
        loading = await storage.create(db, folder, LIMIT, pdb_id="1tqn", assembly="preferred")
        await storage.claim(db, WORKER, VERSION, TIMEOUT)
        await storage.claim(db, WORKER, VERSION, TIMEOUT)
        assert (await storage.get(db, folder, s.id)).job(1).mole_version == VERSION
        assert (await storage.get(db, folder, loading.id)).mole_version == VERSION
    run(database, check)


def test_a_lost_candidate_keeps_no_lock(database, folder):
    """A worker whose candidate run was cancelled while it waited for its session tries the next one without the first
    session's lock: another transaction holding the next session's lock and wanting the first's never deadlocks"""
    async def check(db: Database):
        first = await storage.create(db, folder, LIMIT, pdb_id="1tqn", assembly="preferred")
        second = await storage.create(db, folder, LIMIT, pdb_id="2bg9", assembly="preferred")
        lock = "SELECT id FROM sessions WHERE id = :id FOR UPDATE"
        async with db.engine.connect() as one, db.engine.connect() as other:
            await one.begin()
            await one.execute(text(lock), {"id": first.id})
            claiming = asyncio.create_task(storage.claim(db, WORKER, VERSION, TIMEOUT))  # waits for the first session
            await asyncio.sleep(0.3)
            await other.begin()
            await other.execute(text("SET LOCAL lock_timeout = '3s'"))
            await other.execute(text(lock), {"id": second.id})
            await one.execute(text("UPDATE runs SET phase = 'done' WHERE session_id = :id"), {"id": first.id})  # cancelled
            await one.commit()
            await asyncio.sleep(0.3)  # the worker tries the second, waits for its session
            await other.execute(text(lock), {"id": first.id})  # free: no deadlock, no timeout
            await other.commit()
        assert (await claiming).session_id == second.id
    run(database, check)


def test_one_of_two_jobs_submitted_at_once_is_refused(database, folder):
    async def check(db: Database):
        s = await ready_session(db, folder)
        results = await asyncio.gather(*[storage.add_job(db, s, "tunnels", {}, LIMIT) for _ in range(2)],
                                       return_exceptions=True)
        assert sorted(type(r).__name__ for r in results) == ["Job", "Refused"]
        assert next(r for r in results if isinstance(r, Refused)).status == 409
    run(database, check)


def test_the_queue_limit_holds_for_requests_at_once(database, folder):
    async def check(db: Database):
        results = await asyncio.gather(*[storage.create(db, folder, storage.Admission(3), pdb_id="1tqn", assembly="preferred")
                                         for _ in range(8)], return_exceptions=True)
        assert sum(isinstance(r, storage.StoredSession) for r in results) == 3
        assert all(r is storage.QUEUE_FULL for r in results if not isinstance(r, storage.StoredSession))
        assert len(list(folder.iterdir())) == 3  # the refused ones made no folder
    run(database, check)


@pytest.mark.parametrize("address, client", [
    ("192.0.2.7", "192.0.2.7"),
    ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),  # one machine may use its whole /64 network
    ("2001:db8:1:2:bbbb::9", "2001:db8:1:2::/64"),
    ("::ffff:192.0.2.7", "192.0.2.7"),  # IPv4 mapped to IPv6
    ("testclient", "testclient"),  # not an address (tests)
    (None, None),
])
def test_clients_of_addresses(address, client):
    assert storage.client_of(address) == client


def client(name: str, per_client: int | None = None, per_hour: int | None = None) -> storage.Admission:
    return storage.Admission(100, name, per_client, per_hour)


def test_a_client_is_limited_in_runs_queued_or_running(database, folder):
    """A client cannot fill the queue, other clients still get in; also for requests at once"""
    async def check(db: Database):
        results = await asyncio.gather(*[storage.create(db, folder, client("bot", per_client=3), pdb_id="1tqn", assembly="preferred")
                                         for _ in range(8)], return_exceptions=True)
        refused = [r for r in results if isinstance(r, Refused)]
        assert len(refused) == 5 and {(r.status, r.headers["Retry-After"]) for r in refused} == {(429, "60")}
        assert refused[0].detail.startswith("You have 3 unfinished computations, at most 3 can wait or run at once.")
        assert await storage.create(db, folder, client("person", per_client=3), pdb_id="1tqn", assembly="preferred")
        assert (await storage.refusal(db, client("bot", per_client=3))).status == 429  # the upload's check before reading

        taken = await storage.claim(db, WORKER, VERSION, TIMEOUT)
        assert await storage.end(db, taken)  # a run of the bot done: room for another one
        assert await storage.create(db, folder, client("bot", per_client=3), pdb_id="1tqn", assembly="preferred")
    run(database, check)


def test_a_client_is_limited_in_runs_added_in_an_hour(database, folder):
    async def check(db: Database):
        for _ in range(2):
            await storage.create(db, folder, client("bot", per_hour=2), pdb_id="1tqn", assembly="preferred")
            assert await storage.end(db, await storage.claim(db, WORKER, VERSION, TIMEOUT))  # done ones count too
        with pytest.raises(Refused) as refused:
            await storage.create(db, folder, client("bot", per_hour=2), pdb_id="1tqn", assembly="preferred")
        assert refused.value.status == 429
        assert refused.value.detail.startswith("You have started 2 computations in the last hour, at most 2 are possible.")
        assert 3500 < int(refused.value.headers["Retry-After"]) <= 3601  # until the first one is an hour old
        assert await storage.create(db, folder, client("person", per_hour=2), pdb_id="1tqn", assembly="preferred")

        async with db.engine.begin() as c:  # an hour later
            await c.execute(text("UPDATE runs SET queued = queued - interval '61 minutes'"))
        assert await storage.create(db, folder, client("bot", per_hour=2), pdb_id="1tqn", assembly="preferred")
    run(database, check)


def programmatic(name: str, limit: int | None = None, per_hour: int | None = None) -> storage.Admission:
    return storage.Admission(100, name, programmatic=True, programmatic_limit=limit, programmatic_per_hour=per_hour)


def test_programmatic_runs_are_limited_together(database, folder):
    """Scripts and assistants, from any addresses, fill only their part of the queue; the website's users keep the rest"""
    async def check(db: Database):
        for name in ["bot-1", "bot-2"]:
            await storage.create(db, folder, programmatic(name, limit=2), pdb_id="1tqn", assembly="preferred")
        with pytest.raises(Refused) as refused:
            await storage.create(db, folder, programmatic("bot-3", limit=2), pdb_id="1tqn", assembly="preferred")
        assert (refused.value.status, refused.value.detail) == (503, storage.PROGRAMMATIC_FULL)
        assert refused.value.headers == {"Retry-After": "60"}
        assert await storage.create(db, folder, client("person"), pdb_id="1tqn", assembly="preferred")

        assert await storage.end(db, await storage.claim(db, WORKER, VERSION, TIMEOUT))  # a bot's done: room for another
        assert await storage.create(db, folder, programmatic("bot-3", limit=2), pdb_id="1tqn", assembly="preferred")
    run(database, check)


def test_programmatic_runs_are_limited_together_in_an_hour(database, folder):
    async def check(db: Database):
        for name in ["bot-1", "bot-2"]:
            await storage.create(db, folder, programmatic(name, per_hour=2), pdb_id="1tqn", assembly="preferred")
            assert await storage.end(db, await storage.claim(db, WORKER, VERSION, TIMEOUT))  # done ones count too
        with pytest.raises(Refused) as refused:
            await storage.create(db, folder, programmatic("bot-3", per_hour=2), pdb_id="1tqn", assembly="preferred")
        assert refused.value.status == 503 and 3500 < int(refused.value.headers["Retry-After"]) <= 3601
        assert await storage.create(db, folder, client("person"), pdb_id="1tqn", assembly="preferred")
    run(database, check)


def test_a_free_worker_takes_a_run_of_the_client_with_fewest_running(database, folder):
    """A client with many queued runs only uses the workers others leave free; the oldest first among equals"""
    async def check(db: Database):
        bot = [await storage.create(db, folder, client("bot"), pdb_id="1tqn", assembly="preferred") for _ in range(3)]
        person = await storage.create(db, folder, client("person"), pdb_id="1tqn", assembly="preferred")
        other = await storage.create(db, folder, client("other"), pdb_id="1tqn", assembly="preferred")
        taken = [await storage.claim(db, f"worker-{i}", VERSION, TIMEOUT) for i in range(4)]
        # bot (the oldest, nobody runs), person (bot runs one), other, then bot again
        assert [r.session_id for r in taken] == [bot[0].id, person.id, other.id, bot[1].id]
    run(database, check)


async def stale(db: Database, run: storage.Run) -> None:
    """Its lease was last renewed long ago, as of a worker that stopped renewing it"""
    async with db.engine.begin() as c:
        await c.execute(text("UPDATE runs SET heartbeat = now() - interval '1 hour' WHERE id = :id"), {"id": run.id})


def test_a_held_lease_is_renewed_and_does_not_expire(database, folder):
    async def check(db: Database):
        s = await storage.create(db, folder, LIMIT, pdb_id="1tqn", assembly="preferred")
        taken = await storage.claim(db, WORKER, VERSION, TIMEOUT)
        assert await storage.renew(db, taken)
        assert await storage.expire(db, TIMEOUT) == 0
        assert not await storage.renew(db, dataclasses.replace(taken, worker="worker-2"))  # only its worker holds it
        assert await storage.store_structure(db, taken, "1tqn.cif", "1", True)
        assert await storage.end(db, taken)
        assert (await storage.get(db, folder, s.id)).state == SessionState.ready
    run(database, check)


def test_an_expired_lease_is_never_renewed_nor_written_with(database, folder):
    """Its worker comes back (e.g. the database was away) before the cleanup marked the run: it can neither renew the
    lease nor store anything; the cleanup then fails the run as interrupted"""
    async def check(db: Database):
        s = await ready_session(db, folder)
        await storage.add_job(db, s, "tunnels", {}, LIMIT)
        loading = await storage.create(db, folder, LIMIT, pdb_id="1tqn", assembly="preferred")
        job_run, load_run = await storage.claim(db, WORKER, VERSION, TIMEOUT), await storage.claim(db, WORKER, VERSION, TIMEOUT)
        for taken in (job_run, load_run):
            await stale(db, taken)
            assert not await storage.renew(db, taken)
            assert not await storage.end(db, taken)
        assert not await storage.store_structure(db, load_run, "1tqn.cif", "1", True)
        assert await storage.expire(db, TIMEOUT) == 2
        assert await storage.expire(db, TIMEOUT) == 0
        job = (await storage.get(db, folder, s.id)).job(1)
        assert (job.state, job.error) == (JobState.failed, INTERRUPTED) and job.finished is not None
        failed = await storage.get(db, folder, loading.id)
        assert (failed.state, failed.error, failed.structure_file) == (SessionState.failed, INTERRUPTED, None)
        assert (await storage.get(db, folder, s.id)).state == SessionState.ready  # the structure is still loaded
    run(database, check)


async def locked_meanwhile(db: Database, session_id: str, seconds: float, waiting) -> object:
    """waiting's result; it waits for the session's lock, held by another transaction for seconds"""
    async with db.engine.connect() as other:
        await other.begin()
        await other.execute(text("SELECT id FROM sessions WHERE id = :id FOR UPDATE"), {"id": session_id})
        task = asyncio.create_task(waiting)
        await asyncio.sleep(seconds)
        await other.commit()
    return await task


@pytest.mark.parametrize("write", ["end", "store_structure"])
def test_a_lease_expiring_while_a_write_waits_for_the_lock_is_not_written_with(database, folder, write):
    """The lease is checked when the write runs, not when its transaction began: it waited for the session's lock
    meanwhile, past the lease's timeout"""
    async def check(db: Database):
        s = await storage.create(db, folder, LIMIT, pdb_id="1tqn", assembly="preferred")
        taken = await storage.claim(db, WORKER, VERSION, 1)
        writing = storage.end(db, taken) if write == "end" else storage.store_structure(db, taken, "1tqn.cif", "1", True)
        assert not await locked_meanwhile(db, s.id, 1.5, writing)
        stored = await storage.get(db, folder, s.id)
        assert (stored.state, stored.structure_file) == (SessionState.loading, None)
    run(database, check)


def test_a_claim_waiting_for_the_lock_gets_a_whole_lease(database, folder):
    """The lease starts when the claim takes the run, not when its transaction began, before it waited for the
    session's lock longer than the lease's timeout"""
    async def check(db: Database):
        s = await storage.create(db, folder, LIMIT, pdb_id="1tqn", assembly="preferred")
        taken = await locked_meanwhile(db, s.id, 1.5, storage.claim(db, WORKER, VERSION, 1))
        assert await storage.renew(db, taken)
    run(database, check)


def test_the_api_fails_runs_whose_lease_expired(settings, monkeypatch):
    """The API's cleanup (every third of the timeout): a worker stopped renewing the lease of its run"""
    settings = settings.model_copy(update={"worker_timeout_seconds": 5})
    ids = {}

    async def left(db: Database):
        ids["session"] = (await storage.create(db, settings.working_directory, LIMIT, pdb_id="1tqn", assembly="preferred")).id
        await stale(db, await storage.claim(db, WORKER, VERSION, 5))
    run(settings.database_url, left)
    with TestClient(create_app(settings)) as client:
        end = time.monotonic() + 10
        while (session := client.get(f"/sessions/{ids['session']}").json())["state"] == "loading" and time.monotonic() < end:
            time.sleep(0.1)
        assert (session["state"], session["error"]) == ("failed", INTERRUPTED)


@pytest.mark.parametrize("steps", range(0, 30, 3))
def test_requests_cancelled_in_a_transaction_leave_no_trace(database, folder, steps):
    """A request cancelled midway (the client gone, the API stopping) ends its transaction: all of it or nothing is
    stored, its session stays usable, the connections are returned clean"""
    async def check(db: Database):
        s = await ready_session(db, folder)
        adding = asyncio.create_task(storage.add_job(db, s, "tunnels", {}, LIMIT))
        for _ in range(steps):  # cancelled at different points
            await asyncio.sleep(0)
        adding.cancel()
        await asyncio.gather(adding, return_exceptions=True)
        async with db.engine.connect() as c:
            jobs = (await c.execute(text("SELECT count(*) FROM jobs"))).scalar()
            runs = (await c.execute(text("SELECT count(*) FROM runs WHERE number IS NOT NULL"))).scalar()
        assert jobs == runs <= 1  # a job always with its run
        for _ in range(5):  # the pool's connections work, none holds the session's lock
            assert (await storage.get(db, folder, s.id)).id == s.id
        if jobs == 0:
            assert (await storage.add_job(db, s, "tunnels", {}, LIMIT)).number == 1
    run(database, check)


@pytest.mark.parametrize("id", ["NoSuchSession", "..x", "a.b"])
def test_unknown_session(client, id):
    response = client.get(f"/sessions/{id}")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {"type": "about:blank", "title": "Not Found", "status": 404, "detail": f"Session [{id}] does not exist."}


@pytest.mark.parametrize("url", ["postgresql://u:p%40ss@db:5432/mole", "postgres://u:p%40ss@db:5432/mole",
                                 "postgresql+asyncpg://u:p%40ss@db:5432/mole"])
def test_urls_of_postgresql_tools_use_asyncpg(url):
    """As CloudNativePG's secret (uri) gives it, the password encoded"""
    assert storage.with_driver(url) == "postgresql+asyncpg://u:p%40ss@db:5432/mole"
