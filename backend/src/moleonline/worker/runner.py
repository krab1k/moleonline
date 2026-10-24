"""The API's slots: each takes the oldest queued run from the database and runs it, one at a time. A run is a session's
load (its structure downloaded from PDBe first, unless it was uploaded) or one of its jobs: its processes (MOLE, the
pores tool) one after another, then its finish (zipping the results). A run is stopped when it is no longer running in
the database (cancelled, deleted): its processes are killed with their children, its state was set by whoever stopped
it. Runs still running when the API stops are interrupted; the next API marks them so (storage.interrupt).

A run whose state cannot be kept (the database failed while it ran) stops the API, which is restarted (compose,
Kubernetes): a healthy API never keeps a run it no longer runs. Failing to take a run loses nothing, the slot tries
again."""

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2

from ..core import storage
from ..core.config import Settings
from ..core.storage import Database, Run
from . import pdbe, work
from .work import Step

log = logging.getLogger("uvicorn.error")  # the API's log, in uvicorn's format, one line per event

IDLE_SECONDS = 0.5  # a slot without a run looks for one this often; the tests shorten it
CHECK_SECONDS = 0.5  # a run is checked this often: cancelled or deleted, it is stopped
STOPPED = object()  # what a run's part returns when the run was stopped meanwhile


def terminate() -> None:
    """Stops the API as SIGTERM does (its runs' processes are killed); the tests replace it"""
    os.kill(os.getpid(), signal.SIGTERM)


class Runner:
    """max_concurrent_computations slots"""

    def __init__(self, settings: Settings, db: Database, http: Callable[[], httpx2.AsyncClient], mole_version: str):
        """http: PDBe's client, when it is needed (the tests replace it); mole_version: recorded with the runs"""
        self.settings, self.db, self.http, self.mole_version = settings, db, http, mole_version
        self.minutes = settings.max_computation_minutes
        self.time_limit = self.minutes * 60.0  # seconds of a run's processes, the download not included
        self.tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self.tasks = [asyncio.create_task(self._slot()) for _ in range(self.settings.max_concurrent_computations)]

    async def stop(self) -> None:
        """Kills the runs' processes; the runs stay running, the next API marks them interrupted"""
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _slot(self) -> None:
        while True:
            try:
                run = await storage.claim(self.db, self.mole_version)
            except Exception as e:  # noqa: BLE001 (e.g. the database is not available for a moment; nothing is lost)
                log.warning("A slot could not take a run: %s", e)
                await asyncio.sleep(storage.CONNECT_PAUSE)
                continue
            if run is None:
                await asyncio.sleep(IDLE_SECONDS)
                continue
            try:
                await self._run(run)
            except Exception:
                log.exception("The state of a run could not be kept, the API stops; it is marked interrupted when it starts again")
                terminate()
                return

    async def _run(self, run: Run) -> None:
        s = await storage.get(self.db, self.settings.working_directory, run.session_id)
        if run.number is None and s.structure_file is None:  # a PDB entry's
            got = await self._while_running(run, pdbe.download(self.http(), s.folder(), s.pdb_id, s.assembly))
            if got is STOPPED:
                return
            if isinstance(got, pdbe.NotDownloaded):
                await storage.end(self.db, run, got.error, got.assembly_id)
                return
            if not await storage.store_structure(self.db, run, got.structure_file, got.assembly_id, got.opm):
                return
            s = await storage.get(self.db, self.settings.working_directory, run.session_id)
        failure = await self._while_running(run, self._compute(run, s))
        if failure is not STOPPED:
            await storage.end(self.db, run, failure)

    async def _while_running(self, run: Run, part: Awaitable[Any]) -> Any:
        """The part's result, or STOPPED when the run stopped meanwhile: the part is then cancelled, its processes
        killed; it ends before the slot takes another run"""
        task = asyncio.ensure_future(part)
        try:
            while not (await asyncio.wait({task}, timeout=CHECK_SECONDS))[0]:
                if not await storage.is_running(self.db, run):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return STOPPED
        except BaseException:  # the API stops, or the database failed: the processes are killed
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
    files are not written while the next run of the slot starts"""
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
