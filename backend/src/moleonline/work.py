"""What a run does, made from what is stored: the session, and the job's type and parameters. A run is processes
(Steps), one after another, then a finish in a thread, which returns why the run failed (zipping the results)."""

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import mole
from .config import Settings
from .parameters import MoleParameters, PoresParameters, PoresRequest
from .storage import StoredSession

PORES_PARAMETERS = "pores.json"  # the input of the pores tool
# a process of its own, as MOLE: a stop or the time limit kills it with MEMEMBED; the tests replace it
PORES_TOOL = [sys.executable, "-m", "moleonline.pores"]


@dataclass(frozen=True)
class Step:
    """A process of a run. errors_on_stderr: any output on stderr is an error (MOLE reports errors so, with exit code
    0); otherwise the exit code decides and stderr says why it failed (the pores tool, whose warnings are no error)."""
    command: list[str]
    errors_on_stderr: bool = False


@dataclass(frozen=True)
class Work:
    steps: list[Step]
    finish: Callable[[], str | None] | None = None


def run_mole(settings: Settings, input: Path) -> Step:
    return Step([str(settings.mole), str(input)], errors_on_stderr=True)  # MOLE reports errors on stderr, with exit code 0


def pore_found(directory: Path) -> str | None:
    """Why a pore computation failed although MOLE did not"""
    results = directory / "json" / "data.json"
    if not results.exists():
        return "MOLE did not produce any result."
    if not json.loads(results.read_bytes()).get("Channels", {}).get("Paths"):
        return "No pore was found along the membrane normal."
    return None


def load(settings: Settings, s: StoredSession) -> Work:
    """MOLE reads the structure (load/): its residues and chains, no tunnels"""
    return Work([run_mole(settings, mole.load_input(s))])


def job(settings: Settings, s: StoredSession, number: int) -> Work:
    """The job's input in its folder, and its processes; raises when the input cannot be written"""
    found = s.job(number)
    directory = s.job_directory(number)
    if found.type == "tunnels":
        steps = [run_mole(settings, mole.computation_input(s, MoleParameters.model_validate(found.parameters), directory))]

        def finish() -> str | None:
            mole.zip_report(directory, s.structure_path())
            return None
    else:
        tool_input = directory / PORES_PARAMETERS
        tool_input.write_text(PoresParameters(
            Structure=str(s.structure_path()), PdbId=s.pdb_id if s.opm else None, WorkingDirectory=str(directory),
            Membrane=str(directory / "membrane.json"), Memembed=str(settings.memembed), OpmCache=str(settings.opm_cache),
            **PoresRequest.model_validate(found.parameters).model_dump(),
        ).model_dump_json(indent=2))
        steps = [Step([*PORES_TOOL, str(tool_input)]), run_mole(settings, directory / mole.INPUT)]

        def finish() -> str | None:
            if problem := pore_found(directory):
                return problem
            mole.zip_report(directory, s.structure_path())
            return None
    return Work(steps, finish)
