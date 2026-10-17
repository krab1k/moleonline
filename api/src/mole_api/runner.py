"""Runs MOLE and the pores tool for computations, in the background of the request that started them."""

import asyncio
import contextlib
import os
import signal
from datetime import UTC, datetime

from .computation import Computation, Status
from .config import Settings


class Runner:
    """At most max_concurrent_computations runs at once, loads included; the others wait for a slot. A run's
    processes run one after another and set the status: Error (FailedInitialization for a load: nothing can run on
    it) when a process writes to stderr, exits with a non-zero code, exceeds the time limit or cannot be started,
    otherwise Finished (Initialized)."""

    def __init__(self, settings: Settings):
        self.slots = asyncio.Semaphore(settings.max_concurrent_computations)
        self.minutes = settings.max_computation_minutes
        self.time_limit = self.minutes * 60.0  # seconds of one run, the queue not included
        self.tasks: set[asyncio.Task] = set()

    def start(self, computation: Computation, steps: list[list[str]], init: bool) -> None:
        """steps: the command lines of the processes"""
        task = asyncio.create_task(self._run(computation, steps, init))
        self.tasks.add(task)  # referenced until done
        task.add_done_callback(self.tasks.discard)

    async def stop(self) -> None:
        """The API is stopping: the processes are killed, the statuses stay (the next API marks them interrupted)"""
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _run(self, c: Computation, steps: list[list[str]], init: bool) -> None:
        failed = Status.FailedInitialization if init else Status.Error
        async with self.slots:
            try:
                c.last.Started = datetime.now(UTC)
                c.save()
                try:
                    async with asyncio.timeout(self.time_limit):
                        for step in steps:
                            if error := await run_process(step):
                                c.change_status(failed, error)
                                return
                except TimeoutError:
                    c.change_status(failed, f"The computation exceeded the time limit of {self.minutes} minute{'s' if self.minutes != 1 else ''}.")
                    return
                c.change_status(Status.Initialized if init else Status.Finished)
            except Exception as e:  # e.g. a process cannot be started; never leave the computation running
                with contextlib.suppress(Exception):
                    c.change_status(failed, str(e))


async def run_process(command: list[str]) -> str | None:
    """Runs a process until it exits; returns why it failed. Cancelled (time limit, the API stopping), it is killed
    with its children (the pores tool starts MEMEMBED and MOLE)."""
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                                                   stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        _, stderr = await process.communicate()
    except BaseException:
        with contextlib.suppress(ProcessLookupError):  # also children that keep stderr open after the process exited
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
