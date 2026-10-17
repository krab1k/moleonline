"""A computation: a folder in the working directory with its status file _object.json, the structure, and a folder per
submission (0 is the load). The status file has the format of the former C# API."""

import base64
import os
import re
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

STATUS_FILE = "_object.json"
API_INSTANCE = uuid.uuid4().hex  # this process; Running/Initializing written by another one was interrupted by a restart
COMPUTATION_ID = re.compile(r"^[A-Za-z0-9]+$")  # base64 without /+=, never leaves the working directory


class Status(StrEnum):
    Aborted = "Aborted"
    Initialized = "Initialized"
    Initializing = "Initializing"
    Running = "Running"
    Finished = "Finished"
    Error = "Error"
    Deleted = "Deleted"
    FailedInitialization = "FailedInitialization"


class Report(BaseModel):
    """The state of a submission, the response of most requests; with an HTTP error status, ErrorMsg says why the
    request failed. A report of a computation that failed is not a failed request."""
    ComputationId: str | None  # null when no computation was created (a refused upload)
    SubmitId: int
    Status: Status
    ErrorMsg: str


class Unit(BaseModel):
    """A submission; the first one is the load, a first computation reuses it and replaces its times"""
    SubmitId: int
    Status: Status
    ErrorMsg: str = ""
    Submitted: datetime | None = None  # UTC
    Started: datetime | None = None  # got a slot; Started - Submitted is the time in the queue (and the download)
    Finished: datetime | None = None  # Finished, Error or Aborted
    MoleVersion: str | None = None  # the results, and which parameters were defaults, depend on it


class Computation(BaseModel):
    ComputationId: str
    BaseDir: str  # the working directory
    UserStructure: bool
    PdbId: str = ""  # empty for uploaded structures
    AssemblyId: str | None = None  # None: the full structure
    DbModePores: bool = False
    StructureFile: str | None = None  # in the computation folder, set once the file is complete
    ComputationUnits: list[Unit]
    Created: datetime | None = None  # UTC
    Loaded: datetime | None = None  # the load ended: Initialized or FailedInitialization
    ApiInstance: str | None = None  # the API process that wrote the status last

    @property
    def last(self) -> Unit:
        return self.ComputationUnits[-1]

    def is_deleted(self) -> bool:
        """Deleting a computation marks its last submission; the computation as a whole is gone"""
        return self.last.Status == Status.Deleted

    def report(self, submit_id: int = -1) -> Report:
        """-1: the last submission; 0: the first"""
        if submit_id == -1:
            submit_id = len(self.ComputationUnits)
        submit_id = submit_id or 1
        unit = next((u for u in self.ComputationUnits if u.SubmitId == submit_id), None)
        if unit is None:
            return Report(ComputationId=self.ComputationId, SubmitId=submit_id, Status=Status.Error, ErrorMsg=f"SubmitId {submit_id} not found.")
        return Report(ComputationId=self.ComputationId, SubmitId=unit.SubmitId, Status=unit.Status, ErrorMsg=unit.ErrorMsg)

    def add_submission(self, mole_version: str) -> None:
        """A new submission with its folder; the first one reuses the load's unit (Initialized)"""
        if self.last.Status != Status.Initialized:
            self.ComputationUnits.append(Unit(SubmitId=len(self.ComputationUnits) + 1, Status=Status.Initialized))
        self.last.Submitted, self.last.Started, self.last.Finished = datetime.now(UTC), None, None
        self.last.MoleVersion = mole_version
        self.submit_directory(self.last.SubmitId).mkdir(parents=True, exist_ok=True)
        self.save()

    def change_status(self, status: Status, error: str = "") -> None:
        """Of the last submission, unless the computation is deleted"""
        if self.is_deleted():
            return
        self.last.Status, self.last.ErrorMsg = status, error
        if status in (Status.Initialized, Status.FailedInitialization):
            self.Loaded = self.Loaded or datetime.now(UTC)
        elif status in (Status.Finished, Status.Error, Status.Aborted):
            self.last.Finished = datetime.now(UTC)
        self.save()

    def folder(self) -> Path:
        return Path(self.BaseDir) / self.ComputationId

    def structure_path(self) -> Path:
        if self.StructureFile is None:
            raise ValueError(f"Computation {self.ComputationId} has no structure.")
        return self.folder() / self.StructureFile

    def submit_directory(self, submit_id: int) -> Path:
        """0: the load"""
        return self.folder() / str(submit_id)

    def status_path(self) -> Path:
        return Path(self.BaseDir) / self.ComputationId / STATUS_FILE

    def save(self) -> None:
        """Saves this copy of the computation. Several copies exist (a request, the run, a download), one may be stale:
        a computation deleted meanwhile is never written over, the check and the write are one step for all copies."""
        path = self.status_path()
        with _status_writes:
            if _saved_as_deleted(path):
                return
            self.ApiInstance = API_INSTANCE
            # a new file renamed over the old one, so that readers (status polling) never see a partial file
            with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f"{STATUS_FILE}.", suffix=".tmp", delete=False) as temp:
                temp.write(self.model_dump_json(indent=2))
            os.chmod(temp.name, 0o644)  # as the other files, a temporary file is private
            Path(temp.name).replace(path)


_status_writes = threading.Lock()


def _saved_as_deleted(path: Path) -> bool:
    try:
        return Computation.model_validate_json(path.read_bytes()).is_deleted()
    except (FileNotFoundError, ValueError):  # a corrupt file is replaced
        return False


def create(working_directory: Path, mole_version: str, pdb_id: str = "") -> Computation:
    """A new computation, Initializing: its structure is loaded as submission 1"""
    now = datetime.now(UTC)
    computation = Computation(
        ComputationId=re.sub("[/+=]", "", base64.b64encode(uuid.uuid4().bytes).decode()), BaseDir=str(working_directory),
        UserStructure=not pdb_id, PdbId=pdb_id, Created=now,
        ComputationUnits=[Unit(SubmitId=1, Status=Status.Initializing, Submitted=now, MoleVersion=mole_version)])
    computation.folder().mkdir()
    computation.save()
    return computation


def not_exists(computation_id: str) -> Report:
    return Report(ComputationId=computation_id, SubmitId=0, Status=Status.Error, ErrorMsg=f"ComputationId [{computation_id}] does not exists.\n")


def load(working_directory: Path, computation_id: str) -> Computation | None:
    """None when it does not exist. A run or load interrupted by a restart of the API is marked as failed."""
    if not COMPUTATION_ID.match(computation_id):
        return None
    try:
        computation = Computation.model_validate_json((working_directory / computation_id / STATUS_FILE).read_bytes())
    except FileNotFoundError:
        return None
    status = computation.last.Status
    if status in (Status.Running, Status.Initializing) and computation.ApiInstance != API_INSTANCE:
        computation.change_status(Status.Error if status == Status.Running else Status.FailedInitialization,
                                  "The computation was interrupted by a restart of the server. Please, run it again.")
    return computation
