"""Sessions, their jobs and the queue of their runs, in PostgreSQL. A session's files are in its folder in the working
directory: the structure, the load's folder (load/) and a folder per job (jobs/<number>/).

A run is a session's load or one of its jobs: queued, running, then done. Every change of a state is one transaction
that applies only from the state it expects, so that a cancel or a delete is never overwritten by a run's late
outcome; a run is not done exactly while its load or job is in progress. Every transaction changing a session, its
jobs or its runs locks the session's row first, so that two of them never wait for each other."""

import asyncio
import base64
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from pydantic import BaseModel, ConfigDict, PrivateAttr
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, MetaData, String, Table, Text, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

log = logging.getLogger("uvicorn.error")  # the API's log, in uvicorn's format, one line per event

SESSION_ID = re.compile(r"^[A-Za-z0-9]+$")  # base64 without /+=, never leaves the working directory
INTERRUPTED = "The computation was interrupted by a restart of the server. Please, run it again."
STOPPED_LOAD = "Loading of the structure was stopped."


class SessionState(StrEnum):
    queued = "queued"  # waits for a slot: to download or store the structure, then load it
    loading = "loading"  # the structure is downloaded and MOLE loads it
    ready = "ready"  # jobs can run on it
    failed = "failed"  # it could not be loaded (also cancelled, or interrupted by a restart); nothing can run on it


class JobState(StrEnum):
    queued = "queued"  # waits for a slot
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
    started: datetime | None = None  # got a slot
    finished: datetime | None = None
    mole_version: str  # of the MOLE that ran it, once it started: the results, and which parameters were defaults, depend on it


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
    mole_version: str  # of the MOLE that loaded it, once the load started
    jobs: list[Job] = []


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

    def load_directory(self) -> Path:
        return self.folder() / "load"

    def job_directory(self, number: int) -> Path:
        return self.folder() / "jobs" / str(number)

    def job(self, number: int) -> Job | None:
        return next((j for j in self.jobs if j.number == number), None)


class Refused(Exception):
    """Why a new session or job is not accepted: the HTTP status, the detail, the response's headers"""

    def __init__(self, status: int, detail: str, headers: dict[str, str] | None = None):
        super().__init__(detail)
        self.status, self.detail, self.headers = status, detail, headers


RETRY_AFTER = 60  # seconds, when the queue is full
QUEUE_FULL = Refused(503, "The server is busy, its queue is full. Please, try again later.", {"Retry-After": str(RETRY_AFTER)})


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
    Column("mole_version", String(32), nullable=False),
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
    Column("mole_version", String(32), nullable=False),
)

runs = Table(
    "runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(32), ForeignKey("sessions.id"), nullable=False),
    Column("number", Integer),  # the job's; None: the session's load
    Column("phase", String(8), nullable=False),  # queued, running, done
    Column("queued", DateTime(timezone=True), nullable=False),
    Column("started", DateTime(timezone=True)),
    Column("ended", DateTime(timezone=True)),
    Index("runs_by_phase", "phase", "id"),
)

requests = Table(
    "requests", metadata,  # each request creating a session or a job, also a refused one: usage, distinct clients
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("time", DateTime(timezone=True), nullable=False),
    Column("request", String(8), nullable=False),  # Init (a session) or Submit (a job)
    Column("address", String(64)),  # the client's
    Column("kind", String(8), nullable=False),  # PDB, Upload, Mole (tunnels), Pores
    Column("session_id", String(40)),  # None: none was created; of a job request, as requested
    Index("requests_by_time", "time"),
)

QUEUED, RUNNING, DONE = "queued", "running", "done"
QUEUE_LOCK = 724_212  # advisory lock of the queue: its length is checked and added to in one step
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


class Database:
    """url: postgresql+asyncpg://user:password@host/database"""

    def __init__(self, url: str):
        self.engine = create_async_engine(url, pool_pre_ping=True,
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


async def _queue_has_room(c: AsyncConnection, limit: int) -> None:
    """Refused (QUEUE_FULL) when limit runs are queued or running; with the queue locked until the transaction ends"""
    await c.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": QUEUE_LOCK})
    if await _outstanding(c) >= limit:
        raise QUEUE_FULL


async def create(db: Database, working_directory: Path, limit: int, mole_version: str, pdb_id: str | None = None,
                 assembly: str | None = None, structure: Path | None = None, structure_file: str | None = None) -> StoredSession:
    """A new session, queued with its load: of a PDB entry (downloaded by the load), or of an uploaded structure
    (moved into its folder as structure_file). The folder is made first: a session never lacks its files, at most a
    folder lacks a session. Refused (QUEUE_FULL) when limit runs are queued or running."""
    session = StoredSession(id=new_id(), pdb_id=pdb_id, assembly=assembly, created=now(), mole_version=mole_version,
                            structure_file=structure_file)
    session._directory = working_directory
    async with db.engine.begin() as c:
        await _queue_has_room(c, limit)
        session.folder().mkdir()
        if structure is not None:
            structure.rename(session.structure_path())
        await c.execute(sessions.insert().values(id=session.id, state=session.state, error="", pdb_id=pdb_id, assembly=assembly,
                                                 created=session.created, mole_version=mole_version,
                                                 structure_file=structure_file, opm=False, deleted=False))
        await c.execute(runs.insert().values(session_id=session.id, number=None, phase=QUEUED, queued=session.created))
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


async def add_job(db: Database, s: StoredSession, type: Literal["tunnels", "pores"], parameters: dict, mole_version: str,
                  limit: int) -> Job:
    """A new job of the session, queued with its run, and its folder. Refused (409, 410, QUEUE_FULL) with the session
    locked, so that two requests never start two jobs on it."""
    async with db.engine.begin() as c:
        row = await _lock_session(c, s.id)
        last = (await c.execute(select(jobs).where(jobs.c.session_id == s.id).order_by(jobs.c.number.desc()).limit(1))).mappings().first()
        if refused := _refusal(row, last):
            raise refused
        await _queue_has_room(c, limit)
        job = Job(number=(last["number"] if last else 0) + 1, type=type, parameters=parameters, submitted=now(),
                  mole_version=mole_version)
        # may be there already, empty: left by a job whose transaction failed after it
        s.job_directory(job.number).mkdir(parents=True, exist_ok=True)
        await c.execute(jobs.insert().values(session_id=s.id, **job.model_dump()))
        await c.execute(runs.insert().values(session_id=s.id, number=job.number, phase=QUEUED, queued=job.submitted))
    return job


async def _end_runs(c: AsyncConnection, session_id: str, number: int | None, all_of_session: bool = False) -> None:
    """The runs of the load (number None) or of a job end: a slot running one stops it once it sees it done"""
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


async def queue_is_full(db: Database, limit: int) -> bool:
    """Whether a new session or job would be refused (QUEUE_FULL), without locking the queue"""
    async with db.engine.connect() as c:
        return await _outstanding(c) >= limit


# ---------------------------------------------------------------------------------------------------------- runs

@dataclass(frozen=True)
class Run:
    id: int
    session_id: str
    number: int | None  # the job's; None: the load


async def claim(db: Database, mole_version: str) -> Run | None:
    """The oldest queued run, now running: its load loading or its job running, with the MOLE that runs it (queued
    runs wait over a restart, also one with another MOLE); None when none waits"""
    while True:
        # each candidate in a transaction of its own: one lost to another slot or a cancel keeps no lock while the
        # next is tried, so that two slots never wait for each other's sessions
        async with db.engine.begin() as c:
            row = (await c.execute(select(runs.c.id, runs.c.session_id, runs.c.number).where(runs.c.phase == QUEUED)
                                   .order_by(runs.c.id).limit(1))).first()
            if row is None:
                return None
            await _lock_session(c, row.session_id)
            started = now()
            taken = await c.execute(update(runs).where(runs.c.id == row.id, runs.c.phase == QUEUED).values(phase=RUNNING, started=started))
            if taken.rowcount != 1:  # another slot took it, or it was cancelled, while the session was locked
                continue
            if row.number is None:
                await c.execute(update(sessions).where(sessions.c.id == row.session_id)
                                .values(state=SessionState.loading, mole_version=mole_version))
            else:
                await c.execute(update(jobs).where(jobs.c.session_id == row.session_id, jobs.c.number == row.number)
                                .values(state=JobState.running, started=started, mole_version=mole_version))
            return Run(row.id, row.session_id, row.number)


async def is_running(db: Database, run: Run) -> bool:
    """Not cancelled, deleted or interrupted"""
    async with db.engine.connect() as c:
        return await c.scalar(select(func.count()).select_from(runs).where(runs.c.id == run.id, runs.c.phase == RUNNING)) == 1


async def store_structure(db: Database, run: Run, structure_file: str, assembly_id: str | None, opm: bool) -> bool:
    """The downloaded structure of the run's session, while the run is running; False otherwise"""
    async with db.engine.begin() as c:
        await _lock_session(c, run.session_id)
        if not await c.scalar(select(func.count()).select_from(runs).where(runs.c.id == run.id, runs.c.phase == RUNNING)):
            return False
        await c.execute(update(sessions).where(sessions.c.id == run.session_id)
                        .values(structure_file=structure_file, assembly_id=assembly_id, opm=opm))
    return True


async def end(db: Database, run: Run, failure: str | None = None, assembly_id: str | None = None) -> bool:
    """The run's outcome: ready or finished, or failed with why; stored only while the run is running (False otherwise,
    it changes nothing). assembly_id: of a failed download, the assembly that was looked for."""
    async with db.engine.begin() as c:
        await _lock_session(c, run.session_id)
        ended = now()
        changed = await c.execute(update(runs).where(runs.c.id == run.id, runs.c.phase == RUNNING).values(phase=DONE, ended=ended))
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


async def interrupt(db: Database) -> int:
    """All running runs fail as INTERRUPTED, how many: those of the API process before this one, which runs none yet"""
    async with db.engine.begin() as c:
        stopped = (await c.execute(update(runs).where(runs.c.phase == RUNNING).values(phase=DONE, ended=now())
                                   .returning(runs.c.session_id, runs.c.number))).all()
        for session_id, number in stopped:
            if number is None:
                await c.execute(update(sessions).where(sessions.c.id == session_id)
                                .values(state=SessionState.failed, error=INTERRUPTED, loaded=now()))
            else:
                await c.execute(update(jobs).where(jobs.c.session_id == session_id, jobs.c.number == number)
                                .values(state=JobState.failed, error=INTERRUPTED, finished=now()))
    return len(stopped)


# ----------------------------------------------------------------------------------------------------- requests

async def log_request(db: Database, request: Literal["Init", "Submit"], address: str | None, kind: str, session_id: str | None) -> None:
    """A request creating a session or a job, also a refused one (session_id None when none was created). Usage
    statistics only: when it cannot be stored, that is logged, and the request is answered as without it."""
    try:
        async with db.engine.begin() as c:
            await c.execute(requests.insert().values(time=now(), request=request, address=address and address[:64], kind=kind,
                                                     session_id=session_id and re.sub("[^A-Za-z0-9]", "?", session_id[:40])))
    except Exception as e:  # noqa: BLE001
        log.warning("The request could not be logged: %s", e)
