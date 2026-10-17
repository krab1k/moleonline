"""Runs MOLE and the pores tool for computations, in the background of the request that started them."""

import asyncio
import contextlib
import os
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from .computation import Computation, Status
from .config import Settings


class Runner:
    """At most max_concurrent_computations runs at once, loads included; the others wait for a slot. A run's
    processes run one after another and set the status: Error (FailedInitialization for a load: nothing can run on
    it) when a process writes to stderr, exits with a non-zero code, exceeds the time limit or cannot be started,
    otherwise Finished (Initialized).

    A run is a task: stopping it (the user, the API stopping) cancels it wherever it is, before its slot, waiting for
    it or in a process, which is then killed with its children. A stopped run never changes the status."""

    def __init__(self, settings: Settings):
        self.slots = asyncio.Semaphore(settings.max_concurrent_computations)
        self.minutes = settings.max_computation_minutes
        self.time_limit = self.minutes * 60.0  # seconds of one run, the queue not included
        self.jobs: dict[str, asyncio.Task] = {}  # computation id -> its run, until its last process exits
        self.tasks: set[asyncio.Task] = set()  # all runs, until done

    def start(self, c: Computation, steps: Callable[[], list[list[str]]], init: bool,
              prepare: Callable[[], Awaitable[bool]] | None = None) -> None:
        """steps: the command lines of the processes, made when the run gets its slot. prepare: before the slot is
        awaited (a download), False when it failed and set the status. The run is registered before the request
        is answered: a stop right after it finds the run."""
        task = asyncio.create_task(self._run(c, steps, init, prepare))
        self.jobs[c.ComputationId] = task
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(lambda _: self._forget(c.ComputationId, task))

    def kill(self, computation_id: str) -> bool:
        """Stops the computation's run; False when it has none. Its status is the caller's."""
        task = self.jobs.pop(computation_id, None)
        if task is None:
            return False
        task.cancel()
        return True

    def _forget(self, computation_id: str, task: asyncio.Task | None) -> None:
        if self.jobs.get(computation_id) is task:  # not a later submission's run
            del self.jobs[computation_id]

    async def stop(self) -> None:
        """The API is stopping: the statuses stay (the next API marks them interrupted)"""
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _run(self, c: Computation, steps: Callable[[], list[list[str]]], init: bool,
                   prepare: Callable[[], Awaitable[bool]] | None) -> None:
        failed = Status.FailedInitialization if init else Status.Error
        try:
            if prepare and not await prepare():
                return
            async with self.slots:
                c.last.Started = datetime.now(UTC)
                c.save()
                try:
                    async with asyncio.timeout(self.time_limit):
                        for command in steps():
                            if error := await run_process(command):
                                c.change_status(failed, error)
                                return
                except TimeoutError:
                    c.change_status(failed, f"The computation exceeded the time limit of {self.minutes} minute{'s' if self.minutes != 1 else ''}.")
                    return
                self._forget(c.ComputationId, asyncio.current_task())  # as soon as the last process exits: no stop now
                c.change_status(Status.Initialized if init else Status.Finished)
        except Exception as e:  # e.g. a process cannot be started; never leave the computation running
            with contextlib.suppress(Exception):
                c.change_status(failed, str(e))


async def run_process(command: list[str]) -> str | None:
    """Runs a process until it exits; returns why it failed. Cancelled (a stop, the time limit), it is killed with its
    children (the pores tool starts MEMEMBED and MOLE), also those that outlived it."""
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                                                   stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        _, stderr = await process.communicate()
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        raise

    code = process.returncode if process.returncode >= 0 else 128 - process.returncode  # killed by a signal: as a shell reports it
    if stderr:  # MOLE reports errors on stderr, with exit code 0
        return stderr.decode(errors="replace")
    if code == 128 + signal.SIGKILL:
        return "The computation was terminated by the system, most likely it ran out of memory."
    if code != 0:
        return f"The computation failed with exit code {code}."
    return None
