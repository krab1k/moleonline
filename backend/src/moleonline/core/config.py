"""Settings from environment variables (WORKING_DIRECTORY, MOLE, ...), checked when the API or a worker starts, so that
a wrong path stops it with a clear message instead of failing the first computation. The defaults are the container's.
CoreSettings are those of both, ApiSettings and WorkerSettings add their own."""

import os
import tempfile
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


def writable(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError as e:
        raise ValueError(f"'{path}' is not writable: {e}") from e
    return path


class CoreSettings(BaseSettings):
    working_directory: Path = Path("/data/computations")  # the sessions' files
    # the sessions, their jobs, the queue of their runs, the request log: postgresql://user:password@host/database
    database_url: str
    # a run whose worker did not renew its lease this long (crashed, killed, its node lost) fails as interrupted
    worker_timeout_seconds: int = Field(60, ge=5)

    @field_validator("working_directory")
    @classmethod
    def writable_directory(cls, path: Path) -> Path:
        return writable(path)


class ApiSettings(CoreSettings):
    """The API's: uvicorn moleonline.api.main:app"""
    # runs (loads, jobs) queued or running together; more are refused (503)
    max_outstanding_computations: int = Field(24, ge=1)
    # of them from one client (an address; an IPv6 /64 network), more are refused (429); by default half of them, so
    # that no client (a script) takes the whole queue
    max_outstanding_per_client: int | None = Field(None, ge=1)
    max_computations_per_hour: int = Field(200, ge=1)  # loads and jobs one client may start in an hour, more: 429

    def per_client(self) -> int:
        return self.max_outstanding_per_client or max(1, self.max_outstanding_computations // 2)


class WorkerSettings(CoreSettings):
    """A worker's: python -m moleonline.worker, one run at a time"""
    max_computation_minutes: int = Field(30, ge=1)  # one run (all its processes), the queue not included
    mole: Path = Path("/opt/mole2/mole2")
    memembed: Path = Path("/opt/memembed/memembed")  # the membrane of pore computations without OPM's
    opm_cache: Path = Path("/data/opm-cache")  # membranes downloaded from OPM, reused for 30 days
    # where runs compute, on the worker's own disk: MOLE writes many small files, slow on a shared volume; only the
    # results the API serves are then stored in working_directory. Each worker's own: emptied when the worker starts.
    scratch_directory: Path = Path(tempfile.gettempdir()) / "moleonline"
    # touched by the worker's loop, also while it waits for the database: its age tells a liveness probe (Kubernetes,
    # compose's healthcheck) that the worker hangs
    heartbeat_file: Path = Path("/tmp/heartbeat")

    @field_validator("opm_cache", "scratch_directory")
    @classmethod
    def writable_cache(cls, path: Path) -> Path:
        return writable(path)

    @field_validator("heartbeat_file")
    @classmethod
    def writable_heartbeat(cls, path: Path) -> Path:
        writable(path.parent)
        return path

    @field_validator("mole", "memembed")
    @classmethod
    def executable(cls, path: Path) -> Path:
        if not path.is_absolute():
            raise ValueError(f"'{path}' must be a full path, executables are not looked up in PATH")
        if not path.is_file():
            raise ValueError(f"'{path}' does not exist")
        if not os.access(path, os.X_OK):
            raise ValueError(f"'{path}' is not executable")
        return path
