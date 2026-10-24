"""A worker: takes the oldest queued run from the database and runs it, one at a time. A run is a session's load (its
structure downloaded from PDBe first, unless it was uploaded) or one of its jobs: its processes (MOLE, the pores tool)
one after another, then its finish (zipping the results).

While it runs, the worker renews the run's lease twice a second. When it no longer holds it (the run was cancelled or
deleted, or its lease expired), it kills the run's processes with their children, waits for the run's threads and
takes the next run; the run's state was set by whoever stopped it. When the database fails while a run runs, or the
outcome cannot be stored, the worker kills the run's processes and stops (it is restarted: compose, Kubernetes); the
run's lease expires and the API's cleanup fails it as interrupted. An idle worker waits for the database."""

import asyncio
import contextlib
import logging
import os
import signal
import socket
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2

from ..core import storage
from ..core.config import WorkerSettings
from ..core.storage import Database, Run
from . import pdbe, work
from .work import Step

log = logging.getLogger("uvicorn.error")  # in uvicorn's format, as the API's log, one line per event

IDLE_SECONDS = 0.5  # an idle worker looks for a run this often; the tests shorten it
CHECK_SECONDS = 0.5  # a running run's lease is renewed this often: when it is no longer held, the run is stopped
STOPPED = object()  # what a run's part returns when the run was stopped meanwhile


class Worker:
    def __init__(self, settings: WorkerSettings, db: Database, http: Callable[[], httpx2.AsyncClient], mole_version: str):
        """http: PDBe's client, when it is needed (the tests replace it); mole_version: recorded with the runs"""
        self.settings, self.db, self.http, self.mole_version = settings, db, http, mole_version
        self.minutes = settings.max_computation_minutes
        self.time_limit = self.minutes * 60.0  # seconds of a run's processes, the download not included
        # holds the leases of its runs: unique, and says where it runs
        self.name = f"{socket.gethostname()[:80]}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    async def run(self) -> None:
        """Runs runs until cancelled (SIGTERM: the running run's processes are killed, its lease expires); raises when
        the database fails while a run runs, or its outcome cannot be stored"""
        said = None
        while True:
            self._alive()
            try:
                run = await storage.claim(self.db, self.name, self.mole_version, self.settings.worker_timeout_seconds)
            except Exception as e:  # noqa: BLE001 (e.g. the database is not available for a moment; nothing is lost)
                if str(e) != said:  # once, until something else is wrong
                    log.warning("Waiting for the database: %s", str(e).splitlines()[0] if str(e) else type(e).__name__)
                    said = str(e)
                await asyncio.sleep(storage.CONNECT_PAUSE)
                continue
            said = None
            if run is None:
                await asyncio.sleep(IDLE_SECONDS)
                continue
            await self._run(run)

    def _alive(self) -> None:
        """Touches the heartbeat file: the worker's loop goes on (a liveness probe looks at its age)"""
        try:
            self.settings.heartbeat_file.touch()
        except OSError as e:
            log.warning("The heartbeat file could not be touched: %s", e)

    async def _run(self, run: Run) -> None:
        s = await storage.get(self.db, self.settings.working_directory, run.session_id)
        if run.number is None and s.structure_file is None:  # a PDB entry's
            got = await self._while_held(run, pdbe.download(self.http(), s.folder(), s.pdb_id, s.assembly))
            if got is STOPPED:
                return
            if isinstance(got, pdbe.NotDownloaded):
                await self._end(run, got.error, got.assembly_id)
                return
            if not await storage.store_structure(self.db, run, got.structure_file, got.assembly_id, got.opm):
                return
            s = await storage.get(self.db, self.settings.working_directory, run.session_id)
        failure = await self._while_held(run, self._compute(run, s))
        if failure is not STOPPED:
            await self._end(run, failure)

    async def _end(self, run: Run, failure: str | None, assembly_id: str | None = None) -> None:
        if not await storage.end(self.db, run, failure, assembly_id):
            log.info("The outcome of a run came after it was stopped, it was not stored")

    async def _while_held(self, run: Run, part: Awaitable[Any]) -> Any:
        """The part's result, or STOPPED when the worker no longer holds the run's lease: the part is then cancelled,
        its processes killed, its threads waited for"""
        task = asyncio.ensure_future(part)
        try:
            while not (await asyncio.wait({task}, timeout=CHECK_SECONDS))[0]:
                self._alive()
                if not await storage.renew(self.db, run):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return STOPPED
        except BaseException:  # the worker stops, or the database failed: the processes are killed
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        return task.result()

    async def _compute(self, run: Run, s: storage.StoredSession) -> str | None:
        """Why the run failed, None when it did not"""
        try:
            planned = await in_thread(work.load if run.number is None else work.job, self.settings, s,
                                      *([] if run.number is None else [run.number]))
            try:
                async with asyncio.timeout(self.time_limit):
                    for step in planned.steps:
                        if error := await run_process(step):
                            return error
            except TimeoutError:
                minutes = f"{self.minutes} minute{'s' if self.minutes != 1 else ''}"
                return f"The computation exceeded the time limit of {minutes}."
            return await in_thread(planned.finish) if planned.finish else None
        except Exception as e:  # noqa: BLE001 (e.g. a process cannot be started; never leave the run in progress)
            return str(e)


async def in_thread(f: Callable[..., Any], *args) -> Any:
    """f(*args) in a thread; cancelled, it still waits for the thread to end (a thread cannot be stopped), so that its
    files are not written while the worker's next run starts"""
    future = asyncio.ensure_future(asyncio.to_thread(f, *args))
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        await asyncio.wait({future})
        raise


async def run_process(step: Step) -> str | None:
    """Runs a process until it exits; returns why it failed. Cancelled (a stop, the time limit), it is killed with its
    children (the pores tool starts MEMEMBED), also those that outlived it."""
    process = await asyncio.create_subprocess_exec(*step.command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                                                   stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        _, stderr = await process.communicate()
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        raise

    code = process.returncode if process.returncode >= 0 else 128 - process.returncode  # killed by a signal: as a shell reports it
    if stderr and (step.errors_on_stderr or code != 0):
        return stderr.decode(errors="replace")
    if code == 128 + signal.SIGKILL:
        return "The computation was terminated by the system, most likely it ran out of memory."
    if code != 0:
        return f"The computation failed with exit code {code}."
    if stderr:  # not an error (see Step), e.g. a numpy warning, which may still point at a problem
        log.warning("%s succeeded with output on stderr: %s", " ".join(step.command), " ".join(stderr.decode(errors="replace").split()))
    return None
