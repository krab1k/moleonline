"""Settings from environment variables (WORKING_DIRECTORY, MOLE, ...), checked when the API starts, so that a wrong path
stops the API with a clear message instead of failing the first computation. The defaults are the container's."""

import os
import tempfile
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    working_directory: Path = Path("/data/computations")  # computations and the submission log
    max_concurrent_computations: int = Field(4, ge=1)
    max_computation_minutes: int = Field(30, ge=1)  # one run (all its processes), the queue not included
    mole: Path = Path("/opt/mole2/mole2")
    pores: Path = Path("/opt/pores/bin/pores")

    @field_validator("working_directory")
    @classmethod
    def writable(cls, path: Path) -> Path:
        try:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=path):
                pass
        except OSError as e:
            raise ValueError(f"'{path}' is not writable: {e}") from e
        return path

    @field_validator("mole", "pores")
    @classmethod
    def executable(cls, path: Path) -> Path:
        if not path.is_absolute():
            raise ValueError(f"'{path}' must be a full path, executables are not looked up in PATH")
        if not path.is_file():
            raise ValueError(f"'{path}' does not exist")
        if not os.access(path, os.X_OK):
            raise ValueError(f"'{path}' is not executable")
        return path
