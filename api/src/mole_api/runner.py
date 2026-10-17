"""Runs MOLE and the pores tool for computations, in the background of the request that started them."""

import asyncio
import contextlib
import os
import signal
from datetime import UTC, datetime

from .computation import Computation, Status
from .config import Settings


class Job:
    """One run, from the request that starts it until its last process exits; the process is None while the run
    waits for a slot or between processes. Submissions of a computation share its id, the stop belongs to the run."""

    def __init__(self):
        self.process: asyncio.subprocess.Process | None = None
        self.killed = False

    def kill(self) -> None:
        """The run stops: its process is killed with its children, no other starts, the status is not changed"""
        self.killed = True
        if self.process is not None:
            kill_group(self.process)


def kill_group(process: asyncio.subprocess.Process) -> None:
    """The process with its children (the pores tool starts MEMEMBED and MOLE), also those that outlived it"""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


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
        self.jobs: dict[str, Job] = {}  # computation id -> its run, waiting for a slot or running

    def start(self, computation: Computation, steps: list[list[str]], init: bool) -> None:
        """steps: the command lines of the processes. The run is registered before the request is answered: a stop
        right after it finds the run."""
        job = self.jobs[computation.ComputationId] = Job()
        task = asyncio.create_task(self._run(computation, steps, init, job))
        self.tasks.add(task)  # referenced until done
        task.add_done_callback(self.tasks.discard)

    def kill(self, computation_id: str) -> bool:
        """Stops the computation's run; False when it has none. Its status is the caller's."""
        job = self.jobs.pop(computation_id, None)
        if job is None:
            return False
        job.kill()
        return True

    def _forget(self, computation_id: str, job: Job) -> None:
        if self.jobs.get(computation_id) is job:  # not a later submission's run
            del self.jobs[computation_id]

    async def stop(self) -> None:
        """The API is stopping: the processes are killed, the statuses stay (the next API marks them interrupted)"""
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _run(self, c: Computation, steps: list[list[str]], init: bool, job: Job) -> None:
        """A run stopped by the user keeps the status set by the stop"""
        failed = Status.FailedInitialization if init else Status.Error
        try:
            async with self.slots:
                if job.killed:  # stopped while waiting
                    return
                try:
                    c.last.Started = datetime.now(UTC)
                    c.save()
                    try:
                        async with asyncio.timeout(self.time_limit):
                            for step in steps:
                                error = await run_process(step, job)
                                if job.killed:
                                    return
                                if error:
                                    c.change_status(failed, error)
                                    return
                    except TimeoutError:
                        if job.killed:  # by the user meanwhile, the stop set the status
                            return
                        c.change_status(failed, f"The computation exceeded the time limit of {self.minutes} minute{'s' if self.minutes != 1 else ''}.")
                        return
                    self._forget(c.ComputationId, job)  # as soon as the last process exits
                    c.change_status(Status.Initialized if init else Status.Finished)
                except Exception as e:  # e.g. a process cannot be started; never leave the computation running
                    with contextlib.suppress(Exception):
                        c.change_status(failed, str(e))
        finally:
            self._forget(c.ComputationId, job)


async def run_process(command: list[str], job: Job) -> str | None:
    """Runs a process of the job until it exits; returns why it failed. Cancelled (time limit, the API stopping), it
    is killed with its children."""
    if job.killed:
        return None
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                                                   stderr=asyncio.subprocess.PIPE, start_new_session=True)
    job.process = process
    try:
        if job.killed:  # while it was starting
            job.kill()
        _, stderr = await process.communicate()
    except BaseException:
        kill_group(process)
        await process.wait()
        raise
    finally:
        job.process = None

    code = process.returncode if process.returncode >= 0 else 128 - process.returncode  # killed by a signal: as a shell reports it
    if stderr:  # MOLE reports errors on stderr, with exit code 0
        return stderr.decode(errors="replace")
    if code == 128 + signal.SIGKILL:
        return "The computation was terminated by the system, most likely it ran out of memory."
    if code != 0:
        return f"The computation failed with exit code {code}."
    return None
