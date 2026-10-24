"""A session: a folder in the working directory with its status file session.json, the structure, the load's folder
(load/) and a folder per job (jobs/<number>/). The status file holds the session and its jobs as the API answers them,
and what only the API needs."""

import base64
import os
import re
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, PrivateAttr

STATUS_FILE = "session.json"
API_INSTANCE = uuid.uuid4().hex  # this process; a run in progress written by another one was interrupted by a restart
SESSION_ID = re.compile(r"^[A-Za-z0-9]+$")  # base64 without /+=, never leaves the working directory
INTERRUPTED = "The computation was interrupted by a restart of the server. Please, run it again."


class SessionState(StrEnum):
    queued = "queued"  # the structure is downloaded or stored, the load waits for a slot
    loading = "loading"  # MOLE loads the structure
    ready = "ready"  # jobs can run on it
    failed = "failed"  # it could not be loaded (also cancelled, or interrupted by a restart); nothing can run on it


class JobState(StrEnum):
    queued = "queued"  # waits for a slot
    running = "running"
    finished = "finished"  # its results are available
    failed = "failed"  # the error says why: MOLE's error, the time limit, out of memory, a restart
    cancelled = "cancelled"


IN_PROGRESS = (SessionState.queued, SessionState.loading, JobState.queued, JobState.running)


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
    mole_version: str  # the results, and which parameters were defaults, depend on it


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
    mole_version: str  # of the load
    jobs: list[Job] = []


class StoredSession(Session):
    """The status file: the session, and what only the API needs"""
    structure_file: str | None = None  # in the session's folder, set once the file is complete
    opm: bool = False  # the preferred assembly of a PDB entry, the one OPM has a membrane of
    deleted: bool = False
    api_instance: str = ""  # the API process that wrote the status last
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

    @property
    def last_job(self) -> Job | None:
        return self.jobs[-1] if self.jobs else None

    def job(self, number: int) -> Job | None:
        return next((j for j in self.jobs if j.number == number), None)

    def is_interrupted(self) -> bool:
        """Loading or running in another API process, i.e. one before a restart"""
        in_progress = self.state in IN_PROGRESS or (self.last_job is not None and self.last_job.state in IN_PROGRESS)
        return in_progress and self.api_instance != API_INSTANCE

    def add_job(self, type: Literal["tunnels", "pores"], parameters: dict, mole_version: str) -> Job:
        """A new job, queued, with its folder. The folder may be there already, empty: left by a job whose save failed
        (its input is written after the save), which this one replaces."""
        job = Job(number=len(self.jobs) + 1, type=type, parameters=parameters, submitted=datetime.now(UTC), mole_version=mole_version)
        self.jobs.append(job)
        self.job_directory(job.number).mkdir(parents=True, exist_ok=True)
        self.save()
        return job

    def update(self, number: int | None, state: SessionState | JobState, error: str = "") -> None:
        """The state of the load (number None) or of a job, with its times; nothing changes in a deleted session"""
        now = datetime.now(UTC)
        if number is None:
            self.state, self.error = SessionState(state), error
            if self.state in (SessionState.ready, SessionState.failed):
                self.loaded = now
        else:
            job = self.job(number)
            job.state, job.error = JobState(state), error
            if job.state == JobState.running:
                job.started = now
            elif job.state not in IN_PROGRESS:
                job.finished = now
        self.save()

    def delete(self) -> None:
        self.deleted = True
        self.save()

    def save(self) -> None:
        """Saves this copy of the session. Several copies exist (a request, the run, a download), one may be stale: a
        session deleted meanwhile is never written over, the check and the write are one step for all copies."""
        path = self.folder() / STATUS_FILE
        with _status_writes:
            if not self.deleted and _saved_as_deleted(path):
                return
            self.api_instance = API_INSTANCE
            # a new file renamed over the old one, so that readers (status polling) never see a partial file
            with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f"{STATUS_FILE}.", suffix=".tmp", delete=False) as temp:
                temp.write(self.model_dump_json(indent=2))
            os.chmod(temp.name, 0o644)  # as the other files, a temporary file is private
            Path(temp.name).replace(path)


_status_writes = threading.RLock()  # re-entrant: marking an interrupted run reads and saves under it


def _saved_as_deleted(path: Path) -> bool:
    try:
        return StoredSession.model_validate_json(path.read_bytes()).deleted
    except (FileNotFoundError, ValueError):  # a corrupt file is replaced
        return False


def create(working_directory: Path, mole_version: str, pdb_id: str | None = None) -> StoredSession:
    """A new session, queued: its structure is to be stored or downloaded, then loaded"""
    session = StoredSession(id=re.sub("[/+=]", "", base64.b64encode(uuid.uuid4().bytes).decode()), pdb_id=pdb_id,
                            created=datetime.now(UTC), mole_version=mole_version)
    session._directory = working_directory
    session.folder().mkdir()
    session.save()
    return session


def load(working_directory: Path, session_id: str) -> StoredSession | None:
    """None when it does not exist. A load or job interrupted by a restart of the API is marked as failed."""
    if not SESSION_ID.match(session_id):
        return None
    path = working_directory / session_id / STATUS_FILE
    try:
        session = _read(path, working_directory)
    except FileNotFoundError:
        return None
    if session.is_interrupted():
        # read again under the lock of the writes: meanwhile, another request may have marked it and started a new
        # job, which this copy would write over
        with _status_writes:
            session = _read(path, working_directory)
            if session.is_interrupted():
                if session.state in IN_PROGRESS:
                    session.update(None, SessionState.failed, INTERRUPTED)
                else:
                    session.update(session.last_job.number, JobState.failed, INTERRUPTED)
    return session


def _read(path: Path, working_directory: Path) -> StoredSession:
    session = StoredSession.model_validate_json(path.read_bytes())
    session._directory = working_directory
    return session
