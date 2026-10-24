"""Runs MOLE and the pores tool for sessions (their loads) and jobs, in the background of the request that started them."""

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .config import Settings
from .storage import JobState, SessionState, StoredSession

log = logging.getLogger("uvicorn.error")  # the API's log, in uvicorn's format, one line per event


@dataclass(frozen=True)
class Step:
    """A process of a run. errors_on_stderr: any output on stderr is an error (MOLE reports errors so, with exit code
    0); otherwise the exit code decides and stderr says why it failed (the pores tool, whose warnings are no error)."""
    command: list[str]
    errors_on_stderr: bool = False


class Runner:
    """At most max_concurrent_computations runs at once, loads included; the others wait for a slot. A run is a
    session's load or one of its jobs; one runs on a session at a time. Its processes run one after another and set
    its state: loading (running) once it has a slot, then failed when a process fails (see Step), exceeds the time
    limit or cannot be started, otherwise ready (finished).

    A run is a task: stopping it (the user, the API stopping) cancels it wherever it is, before its slot, waiting for
    it or in a process, which is then killed with its children. A stopped run never changes the state."""

    def __init__(self, settings: Settings):
        self.slots = asyncio.Semaphore(settings.max_concurrent_computations)
        self.minutes = settings.max_computation_minutes
        self.time_limit = self.minutes * 60.0  # seconds of one run, the queue not included
        self.runs: dict[str, asyncio.Task] = {}  # session id -> its run, until its last process exits
        self.tasks: set[asyncio.Task] = set()  # all runs, until done

    def start(self, s: StoredSession, number: int | None, steps: Callable[[], list[Step]],
              prepare: Callable[[], Awaitable[bool]] | None = None, finish: Callable[[], str | None] | None = None) -> None:
        """number: the job's, None for the load. steps: the processes, made when the run gets its slot. prepare: before
        the slot is awaited (a download), False when it failed and set the state. finish: after the last process, in a
        thread (zipping the results), returns why the run failed. The run is registered before the request is answered:
        a stop right after it finds the run."""
        task = asyncio.create_task(self._run(s, number, steps, prepare, finish))
        self.runs[s.id] = task
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(lambda _: self._forget(s.id, task))

    def kill(self, session_id: str) -> bool:
        """Stops the session's run; False when it has none. Its state is the caller's."""
        task = self.runs.pop(session_id, None)
        if task is None:
            return False
        task.cancel()
        return True

    def _forget(self, session_id: str, task: asyncio.Task | None) -> None:
        if self.runs.get(session_id) is task:  # not a later job's run
            del self.runs[session_id]

    async def stop(self) -> None:
        """The API is stopping: the statuses stay (the next API marks them interrupted)"""
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _run(self, s: StoredSession, number: int | None, steps: Callable[[], list[Step]],
                   prepare: Callable[[], Awaitable[bool]] | None, finish: Callable[[], str | None] | None) -> None:
        running, failed, done = ((SessionState.loading, SessionState.failed, SessionState.ready) if number is None
                                 else (JobState.running, JobState.failed, JobState.finished))
        try:
            if prepare and not await prepare():
                return
            async with self.slots:
                s.update(number, running)
                try:
                    async with asyncio.timeout(self.time_limit):
                        for step in steps():
                            if error := await run_process(step):
                                s.update(number, failed, error)
                                return
                except TimeoutError:
                    minutes = f"{self.minutes} minute{'s' if self.minutes != 1 else ''}"
                    s.update(number, failed, f"The computation exceeded the time limit of {minutes}.")
                    return
                self._forget(s.id, asyncio.current_task())  # as soon as the last process exits: no stop now
                if finish and (error := await asyncio.to_thread(finish)):
                    s.update(number, failed, error)
                    return
                s.update(number, done)
        except Exception as e:  # noqa: BLE001 (e.g. a process cannot be started; never leave the run in progress)
            with contextlib.suppress(Exception):
                s.update(number, failed, str(e))


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
