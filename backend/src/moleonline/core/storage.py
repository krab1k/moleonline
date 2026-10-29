"""Sessions, their jobs and the queue of their runs, in PostgreSQL. A session's files are in its folder in the working
directory: the structure, what its load found (data.json) and a folder per job with its results (jobs/<number>/).

A run is a session's load or one of its jobs: queued, running, then done. A worker running it holds its lease, which
it renews; a lease not renewed for the timeout expires (the worker crashed, was killed, lost its node), and the API's
cleanup fails the run as interrupted. A worker writes about a run only while it holds its lease, which it cannot
renew once expired. Every change of a state is one transaction
that applies only from the state it expects, so that a cancel or a delete is never overwritten by a run's late
outcome; a run is not done exactly while its load or job is in progress. Every transaction changing a session, its
jobs or its runs locks the session's row first, so that two of them never wait for each other.

Each run is added by a client (its address), so that no client can take all of the service: a client has a limited
number of runs queued or running and of runs added in an hour, and a free worker takes a run of the client with the
fewest running first."""

import asyncio
import base64
import ipaddress
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from pydantic import BaseModel, ConfigDict, PrivateAttr
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, MetaData, String, Table, Text, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

log = logging.getLogger("uvicorn.error")  # the API's log, in uvicorn's format, one line per event

SESSION_ID = re.compile(r"^[A-Za-z0-9]+$")  # base64 without /+=, never leaves the working directory
INTERRUPTED = "The computation was interrupted by a restart of the server. Please, run it again."
STOPPED_LOAD = "Loading of the structure was stopped."


class SessionState(StrEnum):
    queued = "queued"  # waits for a worker: to download or store the structure, then load it
    loading = "loading"  # the structure is downloaded and MOLE loads it
    ready = "ready"  # jobs can run on it
    failed = "failed"  # it could not be loaded (also cancelled, or interrupted by a restart); nothing can run on it


class JobState(StrEnum):
    queued = "queued"  # waits for a worker
    running = "running"
    finished = "finished"  # its results are available
    failed = "failed"  # the error says why: MOLE's error, the time limit, out of memory, a restart
    cancelled = "cancelled"


IN_PROGRESS = (SessionState.queued, SessionState.loading, JobState.queued, JobState.running)
LOAD_IN_PROGRESS = [SessionState.queued, SessionState.loading]
JOB_IN_PROGRESS = [JobState.queued, JobState.running]


# the defaults are of new sessions and jobs; the API's answers always have every field, as their description says
ANSWER = ConfigDict(json_schema_serialization_defaults_required=True)


class Job(BaseModel):
    """A tunnel or pore computation on a session's structure"""
    model_config = ANSWER
    number: int  # from 1, in the session
    type: Literal["tunnels", "pores"]
    state: JobState = JobState.queued
    error: str = ""  # why it failed, empty otherwise
    # tunnels: MOLE's parameters; pores: those of the pore (InMembrane, IsBetaBarrel, Chains, ProbeRadius,
    # InteriorThreshold); as submitted, with the defaults filled in
    parameters: dict
    submitted: datetime  # UTC
    started: datetime | None = None  # a worker took it
    finished: datetime | None = None
    # of the MOLE that runs it, None until a worker took it: the results, and which parameters were defaults, depend on it
    mole_version: str | None = None


class Session(BaseModel):
    """A structure loaded by MOLE, on which jobs run"""
    model_config = ANSWER
    id: str
    state: SessionState = SessionState.queued
    error: str = ""  # why it failed, empty otherwise
    pdb_id: str | None  # None: an uploaded structure
    assembly_id: str | None = None  # the PDB entry's assembly that was loaded; None: the asymmetric unit, or an upload
    created: datetime  # UTC
    loaded: datetime | None = None  # the load ended (ready or failed)
    mole_version: str | None = None  # of the MOLE that loads it, None until a worker took its load
    jobs: list[Job] = []


# a job's results in its folder: MOLE's data.json (what the web shows), all MOLE's results with the structure, the
# membrane of a pore job
JOB_DATA, JOB_REPORT, JOB_MEMBRANE = "data.json", "report.zip", "membrane.json"


class StoredSession(Session):
    """A session as stored, with what only the API needs; a snapshot, read again to see changes"""
    assembly: str | None = None  # of a PDB entry, as requested: preferred, asymmetric or an assembly id
    structure_file: str | None = None  # in the session's folder, set once the file is complete
    opm: bool = False  # the preferred assembly of a PDB entry, the one OPM has a membrane of
    deleted: bool = False
    _directory: Path = PrivateAttr()  # the working directory

    def public(self) -> Session:
        return Session.model_validate(self.model_dump(include=set(Session.model_fields)))

    def folder(self) -> Path:
        return self._directory / self.id

    def structure_path(self) -> Path:
        if self.structure_file is None:
            raise ValueError(f"Session {self.id} has no structure.")
        return self.folder() / self.structure_file

    def load_data(self) -> Path:
        """MOLE's data.json of the load"""
        return self.folder() / "data.json"

    def job_directory(self, number: int) -> Path:
        """The results of a finished job (JOB_DATA, JOB_REPORT, JOB_MEMBRANE), stored by the worker"""
        return self.folder() / "jobs" / str(number)

    def job(self, number: int) -> Job | None:
        return next((j for j in self.jobs if j.number == number), None)


class Refused(Exception):
    """Why a new session or job is not accepted: the HTTP status, the detail, the response's headers"""

    def __init__(self, status: int, detail: str, headers: dict[str, str] | None = None):
        super().__init__(detail)
        self.status, self.detail, self.headers = status, detail, headers


RETRY_AFTER = 60  # seconds, when the queue is full or the client has too many runs in it
QUEUE_FULL = Refused(503, "The server is busy, its queue is full. Please, try again later.", {"Retry-After": str(RETRY_AFTER)})
PROGRAMMATIC_FULL = ("MOLEonline's capacity for scripts and assistants is used up, the website's users go first. Please, try "
                     "again later, or use the website.")


@dataclass(frozen=True)
class Admission:
    """Who adds a run, and the limits it is checked against (None: none)"""
    limit: int  # runs queued or running together
    client: str | None = None  # who asks, see client_of
    per_client: int | None = None  # of them, from one client
    per_hour: int | None = None  # runs one client may add in an hour
    programmatic: bool = False  # not from the website's page: scripts, assistants (the MCP server); see main.interactive
    programmatic_limit: int | None = None  # of a programmatic one: all programmatic runs queued or running together
    programmatic_per_hour: int | None = None  # all programmatic runs added in an hour


def client_of(address: str | None) -> str | None:
    """The client of an address, for the limits: the address; an IPv6 one as its /64 network, which one machine may
    use as a whole"""
    if not address:
        return None
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address[:64]
    if ip.version == 4:
        return str(ip)
    if ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    return str(ipaddress.ip_network(f"{ip}/64", strict=False))


# ------------------------------------------------------------------------------------------------------- the tables

metadata = MetaData()

sessions = Table(
    "sessions", metadata,
    Column("id", String(32), primary_key=True),
    Column("state", String(16), nullable=False),
    Column("error", Text, nullable=False),
    Column("pdb_id", String(16)),
    Column("assembly", String(16)),
    Column("assembly_id", String(16)),
    Column("created", DateTime(timezone=True), nullable=False),
    Column("loaded", DateTime(timezone=True)),
    Column("mole_version", String(32)),
    Column("structure_file", String(255)),
    Column("opm", Boolean, nullable=False),
    Column("deleted", Boolean, nullable=False),
)

jobs = Table(
    "jobs", metadata,
    Column("session_id", String(32), ForeignKey("sessions.id"), primary_key=True),
    Column("number", Integer, primary_key=True, autoincrement=False),
    Column("type", String(16), nullable=False),
    Column("state", String(16), nullable=False),
    Column("error", Text, nullable=False),
    Column("parameters", JSONB, nullable=False),
    Column("submitted", DateTime(timezone=True), nullable=False),
    Column("started", DateTime(timezone=True)),
    Column("finished", DateTime(timezone=True)),
    Column("mole_version", String(32)),
)

runs = Table(
    "runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(32), ForeignKey("sessions.id"), nullable=False),
    Column("number", Integer),  # the job's; None: the session's load
    Column("phase", String(8), nullable=False),  # queued, running, done
    Column("worker", String(128)),  # running: the worker holding its lease
    Column("heartbeat", DateTime(timezone=True)),  # running: when its lease was last renewed, by the database's clock (clock_timestamp)
    Column("mole_version", String(32)),  # of the worker that took it
    Column("queued", DateTime(timezone=True), nullable=False),
    Column("started", DateTime(timezone=True)),
    Column("ended", DateTime(timezone=True)),
    Column("client", String(64)),  # who added it, see client_of; None: unknown
    Column("programmatic", Boolean, nullable=False),  # added by a script or an assistant, not from the website (Admission)
    Index("runs_by_phase", "phase", "id"),
    Index("runs_by_client", "client", "queued"),
)

requests = Table(
    "requests", metadata,  # each request creating a session or a job, also a refused one: usage, distinct clients
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("time", DateTime(timezone=True), nullable=False),
    Column("request", String(8), nullable=False),  # Init (a session) or Submit (a job)
    Column("address", String(64)),  # the client's
    Column("kind", String(8), nullable=False),  # PDB, Upload, Mole (tunnels), Pores
    Column("session_id", String(40)),  # None: none was created; of a job request, as requested
    Column("programmatic", Boolean, nullable=False),  # of a script or an assistant, not from the website
    Index("requests_by_time", "time"),
)

QUEUED, RUNNING, DONE = "queued", "running", "done"
QUEUE_LOCK = 724_212  # advisory lock of the queue: its length is checked and added to in one step
CLAIM_LOCK = 724_213  # advisory lock of claiming a run, the programmatic ones counted and one taken in one step
MIGRATION_LOCK = 724_213  # processes starting at once migrate one after another
CONNECT_PAUSE = 2.0  # seconds between attempts to connect when starting
# a connection is opened within LOGIN_SECONDS, each command answered within COMMAND_SECONDS, or they fail
LOGIN_SECONDS, COMMAND_SECONDS = 10, 15


def upgrade(connection: Connection) -> None:
    """Alembic's upgrade to the latest revision, on the connection"""
    config = Config()
    config.set_main_option("script_location", "moleonline.core:migrations")
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


def with_driver(url: str) -> str:
    """postgresql://… as PostgreSQL's tools and CloudNativePG's secrets write it, with SQLAlchemy's asyncpg driver"""
    for scheme in ("postgresql://", "postgres://"):
        if url.startswith(scheme):
            return "postgresql+asyncpg://" + url.removeprefix(scheme)
    return url


class Database:
    """url: postgresql://user:password@host/database (or postgresql+asyncpg://…)"""

    def __init__(self, url: str):
        self.engine = create_async_engine(with_driver(url), pool_pre_ping=True,
                                          connect_args={"timeout": LOGIN_SECONDS, "command_timeout": COMMAND_SECONDS})

    async def migrate(self) -> None:
        """Waits until the database accepts connections (Kubernetes starts the API and the database in no order), then
        brings the tables to the code's revision (migrations/), an empty database included, in one transaction"""
        said = None
        while True:
            try:
                async with self.engine.connect():
                    break
            except (OSError, TimeoutError, DBAPIError) as e:
                if str(e) != said:  # once, until something else is wrong
                    log.warning("Waiting for the database: %s", str(e).splitlines()[0] if str(e) else type(e).__name__)
                    said = str(e)
                await asyncio.sleep(CONNECT_PAUSE)
        async with self.engine.begin() as c:
            await c.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": MIGRATION_LOCK})
            await c.run_sync(upgrade)

    async def close(self) -> None:
        await self.engine.dispose()


def now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------------------------- sessions and jobs

def new_id() -> str:
    return re.sub("[/+=]", "", base64.b64encode(uuid.uuid4().bytes).decode())


async def _lock_session(c: AsyncConnection, session_id: str):
    """The session's row, locked until the transaction ends; None when it does not exist"""
    return (await c.execute(select(sessions).where(sessions.c.id == session_id).with_for_update())).mappings().first()


async def _outstanding(c: AsyncConnection) -> int:
    """Runs queued or running"""
    return await c.scalar(select(func.count()).select_from(runs).where(runs.c.phase != DONE))


async def _refusal_of(c: AsyncConnection, a: Admission) -> Refused | None:
    """Why a run is not added: the queue is full (QUEUE_FULL), the client has too many runs queued or running, or
    added too many in the last hour (429)"""
    if await _outstanding(c) >= a.limit:
        return QUEUE_FULL
    if a.programmatic and (refused := await _programmatic_refusal(c, a)):
        return refused
    if a.client is None:
        return None
    if a.per_client is not None:
        mine = await c.scalar(select(func.count()).select_from(runs).where(runs.c.client == a.client, runs.c.phase != DONE))
        if mine >= a.per_client:
            return Refused(429, f"You have {mine} unfinished computations, at most {a.per_client} can wait or run at once. "
                                "Please, wait until some of them finish.", {"Retry-After": str(RETRY_AFTER)})
    if a.per_hour is not None:
        hour_ago = now() - timedelta(hours=1)
        added, oldest = (await c.execute(select(func.count(), func.min(runs.c.queued))
                                          .where(runs.c.client == a.client, runs.c.queued > hour_ago))).one()
        if added >= a.per_hour:
            wait = max(1, int((oldest - hour_ago).total_seconds()) + 1)  # until the oldest leaves the hour
            return Refused(429, f"You have started {added} computations in the last hour, at most {a.per_hour} are possible. "
                                "Please, try again later.", {"Retry-After": str(wait)})
    return None


async def _programmatic_refusal(c: AsyncConnection, a: Admission) -> Refused | None:
    """503 when the programmatic runs, of all their clients together, fill their part of the queue or of the hour: the
    website's users keep the rest"""
    if a.programmatic_limit is not None:
        theirs = await c.scalar(select(func.count()).select_from(runs).where(runs.c.programmatic, runs.c.phase != DONE))
        if theirs >= a.programmatic_limit:
            return Refused(503, PROGRAMMATIC_FULL, {"Retry-After": str(RETRY_AFTER)})
    if a.programmatic_per_hour is not None:
        hour_ago = now() - timedelta(hours=1)
        added, oldest = (await c.execute(select(func.count(), func.min(runs.c.queued))
                                          .where(runs.c.programmatic, runs.c.queued > hour_ago))).one()
        if added >= a.programmatic_per_hour:
            return Refused(503, PROGRAMMATIC_FULL, {"Retry-After": str(max(1, int((oldest - hour_ago).total_seconds()) + 1))})
    return None


async def _admit(c: AsyncConnection, a: Admission) -> None:
    """Raises why the run is not added (_refusal_of); with the queue locked until the transaction ends"""
    await c.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": QUEUE_LOCK})
    if refused := await _refusal_of(c, a):
        raise refused


async def create(db: Database, working_directory: Path, admission: Admission, pdb_id: str | None = None,
                 assembly: str | None = None, structure: Path | None = None, structure_file: str | None = None) -> StoredSession:
    """A new session, queued with its load: of a PDB entry (downloaded by the load), or of an uploaded structure
    (moved into its folder as structure_file). The folder is made first: a session never lacks its files, at most a
    folder lacks a session. Refused (QUEUE_FULL, 429) by the admission's limits."""
    session = StoredSession(id=new_id(), pdb_id=pdb_id, assembly=assembly, created=now(), structure_file=structure_file)
    session._directory = working_directory
    async with db.engine.begin() as c:
        await _admit(c, admission)
        session.folder().mkdir()
        if structure is not None:
            structure.rename(session.structure_path())
        await c.execute(sessions.insert().values(id=session.id, state=session.state, error="", pdb_id=pdb_id, assembly=assembly,
                                                 created=session.created, structure_file=structure_file, opm=False, deleted=False))
        await c.execute(runs.insert().values(session_id=session.id, number=None, phase=QUEUED, queued=session.created,
                                             client=admission.client, programmatic=admission.programmatic))
    return session


async def get(db: Database, working_directory: Path, session_id: str) -> StoredSession | None:
    """None when it does not exist"""
    if not SESSION_ID.match(session_id):
        return None
    async with db.engine.connect() as c:
        row = (await c.execute(select(sessions).where(sessions.c.id == session_id))).mappings().first()
        if row is None:
            return None
        found = (await c.execute(select(jobs).where(jobs.c.session_id == session_id).order_by(jobs.c.number))).mappings().all()
    session = StoredSession.model_validate({**row, "jobs": [dict(j) for j in found]})
    session._directory = working_directory
    return session


def _refusal(row, last_job) -> Refused | None:
    """Why a new job cannot start on the session"""
    if row["deleted"]:
        return Refused(410, "The session has been deleted.")
    if row["state"] in LOAD_IN_PROGRESS:
        return Refused(409, "The structure is still being loaded. This job has not been started. Please, try again later.")
    if row["state"] == SessionState.failed:
        return Refused(409, "The structure could not be loaded, no job can run on it.")
    if last_job is not None and last_job["state"] in JOB_IN_PROGRESS:
        return Refused(409, "The previous job has not finished yet. This one has not been started. Please, try again later.")
    return None


async def add_job(db: Database, s: StoredSession, type: Literal["tunnels", "pores"], parameters: dict, admission: Admission) -> Job:
    """A new job of the session, queued with its run. Refused (409, 410, QUEUE_FULL, 429) with the session
    locked, so that two requests never start two jobs on it."""
    async with db.engine.begin() as c:
        row = await _lock_session(c, s.id)
        last = (await c.execute(select(jobs).where(jobs.c.session_id == s.id).order_by(jobs.c.number.desc()).limit(1))).mappings().first()
        if refused := _refusal(row, last):
            raise refused
        await _admit(c, admission)
        job = Job(number=(last["number"] if last else 0) + 1, type=type, parameters=parameters, submitted=now())
        await c.execute(jobs.insert().values(session_id=s.id, **job.model_dump()))
        await c.execute(runs.insert().values(session_id=s.id, number=job.number, phase=QUEUED, queued=job.submitted,
                                             client=admission.client, programmatic=admission.programmatic))
    return job


async def _end_runs(c: AsyncConnection, session_id: str, number: int | None, all_of_session: bool = False) -> None:
    """The runs of the load (number None) or of a job end: a worker running one stops it once it sees it done"""
    where = [runs.c.session_id == session_id, runs.c.phase != DONE]
    if not all_of_session:
        where.append(runs.c.number.is_(None) if number is None else runs.c.number == number)
    await c.execute(update(runs).where(*where).values(phase=DONE, ended=now()))


async def cancel_load(db: Database, session_id: str) -> bool:
    """The session fails (STOPPED_LOAD) if it is still queued or loading; False otherwise"""
    async with db.engine.begin() as c:
        row = await _lock_session(c, session_id)
        if row is None or row["deleted"] or row["state"] not in LOAD_IN_PROGRESS:
            return False
        await _end_runs(c, session_id, None)
        await c.execute(update(sessions).where(sessions.c.id == session_id)
                        .values(state=SessionState.failed, error=STOPPED_LOAD, loaded=now()))
    return True


async def cancel_job(db: Database, session_id: str, number: int) -> bool:
    """The job is cancelled if it is still queued or running; False otherwise"""
    async with db.engine.begin() as c:
        await _lock_session(c, session_id)
        changed = await c.execute(update(jobs).where(jobs.c.session_id == session_id, jobs.c.number == number,
                                                     jobs.c.state.in_(JOB_IN_PROGRESS))
                                  .values(state=JobState.cancelled, finished=now()))
        if changed.rowcount != 1:
            return False
        await _end_runs(c, session_id, number)
    return True


async def delete(db: Database, session_id: str) -> None:
    """Deleted (410 from then on): what is in progress stops, a load fails and a job is cancelled; the files are kept"""
    async with db.engine.begin() as c:
        await _lock_session(c, session_id)
        await _end_runs(c, session_id, None, all_of_session=True)
        await c.execute(update(sessions).where(sessions.c.id == session_id).values(deleted=True))
        await c.execute(update(sessions).where(sessions.c.id == session_id, sessions.c.state.in_(LOAD_IN_PROGRESS))
                        .values(state=SessionState.failed, error=STOPPED_LOAD, loaded=now()))
        await c.execute(update(jobs).where(jobs.c.session_id == session_id, jobs.c.state.in_(JOB_IN_PROGRESS))
                        .values(state=JobState.cancelled, finished=now()))


async def refusal(db: Database, admission: Admission) -> Refused | None:
    """Why a new session or job would be refused by the admission's limits, without locking the queue"""
    async with db.engine.connect() as c:
        return await _refusal_of(c, admission)


# ---------------------------------------------------------------------------------------------------------- runs

@dataclass(frozen=True)
class Run:
    id: int
    session_id: str
    number: int | None  # the job's; None: the load
    worker: str  # holding its lease
    timeout: float  # seconds after which a lease not renewed expires


def _held(run: Run):
    """The run is running, and its worker holds its lease: renewed within the timeout, by the database's clock when the
    statement runs (clock_timestamp; now() is when the transaction began, before it waited for the session's lock)"""
    return [runs.c.id == run.id, runs.c.phase == RUNNING, runs.c.worker == run.worker,
            runs.c.heartbeat > func.clock_timestamp() - timedelta(seconds=run.timeout)]


async def claim(db: Database, worker: str, mole_version: str, timeout: float, max_running_programmatic: int | None = None) -> Run | None:
    """A queued run, now running with the worker's lease: its load loading or its job running, with the MOLE that runs
    it (queued runs wait over a restart, also one with another MOLE); None when none waits. The website's runs first,
    programmatic ones (scripts, assistants) only while fewer than max_running_programmatic of them run (None: any), so
    that their long runs never take all the workers. Of those, the oldest of the client with the fewest runs running: a
    client with many queued runs only uses the workers others leave free."""
    running = (select(runs.c.client, func.count().label("running")).where(runs.c.phase == RUNNING, runs.c.client.is_not(None))
               .group_by(runs.c.client).subquery())
    where = [runs.c.phase == QUEUED]
    if max_running_programmatic is not None:
        programmatic_running = select(func.count()).select_from(runs).where(runs.c.phase == RUNNING, runs.c.programmatic)
        where.append(or_(~runs.c.programmatic, programmatic_running.scalar_subquery() < max_running_programmatic))
    while True:
        # each candidate in a transaction of its own: one lost to another worker or a cancel keeps no lock while the
        # next is tried, so that two workers never wait for each other's sessions
        async with db.engine.begin() as c:
            if max_running_programmatic is not None:
                # one worker claims at a time: another could otherwise pick another run meanwhile (a newcomer's, with
                # none running, comes first), and both start, past the cap. Before the session's lock, which nobody
                # holds while waiting for this one.
                await c.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CLAIM_LOCK})
            row = (await c.execute(select(runs.c.id, runs.c.session_id, runs.c.number)
                                   .outerjoin(running, runs.c.client == running.c.client).where(*where)
                                   .order_by(runs.c.programmatic, func.coalesce(running.c.running, 0), runs.c.id)
                                   .limit(1))).first()
            if row is None:
                return None
            await _lock_session(c, row.session_id)
            started = now()
            # the lease from now, not from when the transaction began: it may have waited for the session's lock
            taken = await c.execute(update(runs).where(runs.c.id == row.id, runs.c.phase == QUEUED)
                                    .values(phase=RUNNING, worker=worker, heartbeat=func.clock_timestamp(), mole_version=mole_version,
                                            started=started))
            if taken.rowcount != 1:  # another worker took it, or it was cancelled, while the session was locked
                continue
            if row.number is None:
                await c.execute(update(sessions).where(sessions.c.id == row.session_id)
                                .values(state=SessionState.loading, mole_version=mole_version))
            else:
                await c.execute(update(jobs).where(jobs.c.session_id == row.session_id, jobs.c.number == row.number)
                                .values(state=JobState.running, started=started, mole_version=mole_version))
            return Run(row.id, row.session_id, row.number, worker, timeout)


async def renew(db: Database, run: Run) -> bool:
    """Renews the run's lease; False when the worker no longer holds it: the run was cancelled, deleted, or its lease
    expired (it cannot be renewed then, even before the cleanup marked the run)"""
    async with db.engine.begin() as c:
        return (await c.execute(update(runs).where(*_held(run)).values(heartbeat=func.clock_timestamp()))).rowcount == 1


async def store_structure(db: Database, run: Run, structure_file: str, assembly_id: str | None, opm: bool) -> bool:
    """The downloaded structure of the run's session, while the worker holds the run's lease; False otherwise"""
    async with db.engine.begin() as c:
        await _lock_session(c, run.session_id)
        if not await c.scalar(select(func.count()).select_from(runs).where(*_held(run))):
            return False
        await c.execute(update(sessions).where(sessions.c.id == run.session_id)
                        .values(structure_file=structure_file, assembly_id=assembly_id, opm=opm))
    return True


async def end(db: Database, run: Run, failure: str | None = None, assembly_id: str | None = None) -> bool:
    """The run's outcome: ready or finished, or failed with why; stored only while the worker holds the run's lease
    (False otherwise, it changes nothing). assembly_id: of a failed download, the assembly that was looked for."""
    async with db.engine.begin() as c:
        await _lock_session(c, run.session_id)
        ended = now()
        changed = await c.execute(update(runs).where(*_held(run)).values(phase=DONE, ended=ended))
        if changed.rowcount != 1:
            return False
        if run.number is None:
            values = {"state": SessionState.failed if failure else SessionState.ready, "error": failure or "", "loaded": ended}
            if assembly_id is not None:
                values["assembly_id"] = assembly_id
            await c.execute(update(sessions).where(sessions.c.id == run.session_id).values(**values))
        else:
            await c.execute(update(jobs).where(jobs.c.session_id == run.session_id, jobs.c.number == run.number)
                            .values(state=JobState.failed if failure else JobState.finished, error=failure or "", finished=ended))
    return True


async def expire(db: Database, timeout: float) -> int:
    """Runs whose lease was not renewed for timeout seconds fail as INTERRUPTED, how many; each in a transaction of its
    own, its session locked first"""
    stale = runs.c.heartbeat <= func.clock_timestamp() - timedelta(seconds=timeout)
    async with db.engine.connect() as c:
        found = (await c.execute(select(runs.c.id, runs.c.session_id, runs.c.number).where(runs.c.phase == RUNNING, stale))).all()
    expired = 0
    for id, session_id, number in found:
        async with db.engine.begin() as c:
            await _lock_session(c, session_id)
            ended = now()
            if (await c.execute(update(runs).where(runs.c.id == id, runs.c.phase == RUNNING, stale)
                                .values(phase=DONE, ended=ended))).rowcount != 1:
                continue  # its worker ended it meanwhile, or it was cancelled
            if number is None:
                await c.execute(update(sessions).where(sessions.c.id == session_id)
                                .values(state=SessionState.failed, error=INTERRUPTED, loaded=ended))
            else:
                await c.execute(update(jobs).where(jobs.c.session_id == session_id, jobs.c.number == number)
                                .values(state=JobState.failed, error=INTERRUPTED, finished=ended))
            expired += 1
    return expired


async def mole_version(db: Database) -> str | None:
    """Of the MOLE that took the latest run; None before the first one"""
    async with db.engine.connect() as c:
        return await c.scalar(select(runs.c.mole_version).where(runs.c.started.is_not(None)).order_by(runs.c.started.desc()).limit(1))


# ----------------------------------------------------------------------------------------------------- requests

async def log_request(db: Database, request: Literal["Init", "Submit"], address: str | None, kind: str, session_id: str | None,
                      programmatic: bool = False) -> None:
    """A request creating a session or a job, also a refused one (session_id None when none was created). Usage
    statistics only: when it cannot be stored, that is logged, and the request is answered as without it."""
    try:
        async with db.engine.begin() as c:
            await c.execute(requests.insert().values(time=now(), request=request, address=address and address[:64], kind=kind,
                                                     session_id=session_id and re.sub("[^A-Za-z0-9]", "?", session_id[:40]),
                                                     programmatic=programmatic))
    except Exception as e:  # noqa: BLE001
        log.warning("The request could not be logged: %s", e)
