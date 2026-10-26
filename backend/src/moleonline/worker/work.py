"""What a run does, made from what is stored: the session, and the job's type and parameters. A run is processes
(Steps), one after another, in its folder on the worker's own disk (MOLE writes many small files, slow on a shared
volume), then a finish in a thread: it stores the results the API serves in the session's folder and returns why the
run failed."""

import contextlib
import json
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..core.config import WorkerSettings
from ..core.parameters import MoleParameters, PoresParameters, PoresRequest
from ..core.storage import JOB_DATA, JOB_MEMBRANE, JOB_REPORT, StoredSession
from . import mole

PORES_PARAMETERS = "pores.json"  # the input of the pores tool
# a process of its own, as MOLE: a stop or the time limit kills it with MEMEMBED; the tests replace it
PORES_TOOL = [sys.executable, "-m", "moleonline.worker.pores"]


@dataclass(frozen=True)
class Step:
    """A process of a run. errors_on_stderr: any output on stderr is an error (MOLE reports errors so, with exit code
    0); otherwise the exit code decides and stderr says why it failed (the pores tool, whose warnings are no error)."""
    command: list[str]
    errors_on_stderr: bool = False


@dataclass(frozen=True)
class Work:
    """finish: given why the steps failed (None when they did not), stores the results and returns why the run failed"""
    steps: list[Step]
    finish: Callable[[str | None], str | None]


def run_mole(settings: WorkerSettings, input: Path) -> Step:
    return Step([str(settings.mole), str(input)], errors_on_stderr=True)  # MOLE reports errors on stderr, with exit code 0


def pore_found(directory: Path) -> str | None:
    """Why a pore computation failed although MOLE did not"""
    results = directory / "json" / "data.json"
    if not results.exists():
        return "MOLE did not produce any result."
    if not json.loads(results.read_bytes()).get("Channels", {}).get("Paths"):
        return "No pore was found along the membrane normal."
    return None


def scratch(settings: WorkerSettings, s: StoredSession, number: int | None) -> Path:
    """The folder of the session's load (number None) or job on the worker's own disk"""
    return settings.scratch_directory / s.id / ("load" if number is None else str(number))


def clear_scratch(settings: WorkerSettings) -> None:
    """When the worker starts: removes what runs left when the worker was killed (the scratch directory is its own)"""
    shutil.rmtree(settings.scratch_directory, ignore_errors=True)
    settings.scratch_directory.mkdir(parents=True, exist_ok=True)


def prepare(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)


def discard(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)
    with contextlib.suppress(OSError):
        directory.parent.rmdir()  # the session's, when no other run of it left a folder there


def keep(*files: tuple[Path, Path]) -> None:
    """Copies the results that were written (from, to) to the session's folder"""
    for source, target in files:
        if source.is_file():
            shutil.copyfile(source, target)


def load(settings: WorkerSettings, s: StoredSession, directory: Path) -> Work:
    """MOLE reads the structure: its residues and chains, no tunnels"""
    prepare(directory)

    def finish(failure: str | None) -> str | None:
        if failure is None:
            keep((directory / "json" / "data.json", s.load_data()))
        return failure
    return Work([run_mole(settings, mole.load_input(s, directory))], finish)


def job(settings: WorkerSettings, s: StoredSession, directory: Path, number: int) -> Work:
    """The job's input in its folder, and its processes; raises when the input cannot be written"""
    prepare(directory)
    found = s.job(number)
    results = s.job_directory(number)

    def finished() -> None:
        results.mkdir(parents=True, exist_ok=True)
        keep((directory / "json" / "data.json", results / JOB_DATA),
             (mole.zip_report(directory, s.structure_path()), results / JOB_REPORT))

    if found.type == "tunnels":
        steps = [run_mole(settings, mole.computation_input(s, MoleParameters.model_validate(found.parameters), directory))]

        def finish(failure: str | None) -> str | None:
            if failure is None:
                finished()
            return failure
    else:
        tool_input = directory / PORES_PARAMETERS
        tool_input.write_text(PoresParameters(
            Structure=str(s.structure_path()), PdbId=s.pdb_id if s.opm else None, WorkingDirectory=str(directory),
            Membrane=str(directory / JOB_MEMBRANE), Memembed=str(settings.memembed), OpmCache=str(settings.opm_cache),
            **PoresRequest.model_validate(found.parameters).model_dump(),
        ).model_dump_json(indent=2))
        steps = [Step([*PORES_TOOL, str(tool_input)]), run_mole(settings, directory / mole.INPUT)]

        def finish(failure: str | None) -> str | None:
            if (directory / JOB_MEMBRANE).is_file():  # also of a failed job: the web shows where the membrane is
                results.mkdir(parents=True, exist_ok=True)
                keep((directory / JOB_MEMBRANE, results / JOB_MEMBRANE))
            if problem := failure or pore_found(directory):
                return problem
            finished()
            return None
    return Work(steps, finish)
