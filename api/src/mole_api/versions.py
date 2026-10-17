"""Versions of the configured MOLE and pores tool, read when the API starts, so they are always the tools that run.
MOLE without arguments prints "WebChemistry Tunnels 2.5.26.10.17, (c) ..." first, the pores tool with --version
"pores 2.0.0". Without a version the API does not start."""

import asyncio
import os
import re
import signal
from dataclasses import dataclass
from pathlib import Path

from .config import Settings

MOLE_LINE = re.compile(r"^WebChemistry Tunnels (\S+),")
PORES_LINE = re.compile(r"^pores (\S+)$")


class ToolError(Exception):
    """A tool did not report its version"""


@dataclass(frozen=True)
class ToolVersions:
    mole: str
    pores: str


async def read_versions(settings: Settings, timeout: float = 30) -> ToolVersions:
    """timeout: how long a tool may take to report its version"""
    return ToolVersions(
        mole=await read_version("MOLE", settings.mole, [], MOLE_LINE, timeout),
        pores=await read_version("PORES", settings.pores, ["--version"], PORES_LINE, timeout),
    )


async def read_version(setting: str, path: Path, arguments: list[str], version: re.Pattern, timeout: float) -> str:
    """The version from the tool's first line, if it exits with code 0 in time"""
    # stderr goes to the API's log; a session of its own, so that its children are killed with it
    process = await asyncio.create_subprocess_exec(path, *arguments, stdout=asyncio.subprocess.PIPE, start_new_session=True)
    problem = None
    try:
        async with asyncio.timeout(timeout):
            output, _ = await process.communicate()
        if process.returncode != 0:
            problem = f"it exited with code {process.returncode}"
    except TimeoutError:
        problem = f"it did not finish within {timeout:g} s"
        output = b""
    finally:  # also when the API is stopping (cancelled)
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

    line = next(iter(output.decode(errors="replace").splitlines()), "")
    match = version.match(line)
    if problem or not match:
        raise ToolError(f"{setting} '{path}' did not report its version, {problem or f'its first line is "{line}"'}.")
    return match[1]
