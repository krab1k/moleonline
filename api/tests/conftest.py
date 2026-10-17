from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mole_api.config import Settings
from mole_api.main import create_app

MOLE_VERSION = "9.8.7.6"  # what the fake MOLE reports when started without arguments, as MOLE does
PORES_VERSION = "5.4.3"  # what the fake pores tool reports for --version
CSA = Path(__file__).parent.parent / "Resources" / "mcsa_sites.csv.gz"


def script(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o700)
    return path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """The API's own working directory, a fake MOLE and pores tool that only report their versions"""
    return Settings(
        working_directory=tmp_path / "computations",
        csa=CSA,
        mole=script(tmp_path, "mole", f"[ -z \"$1\" ] && echo 'WebChemistry Tunnels {MOLE_VERSION}, (c) test'"),
        pores=script(tmp_path, "pores", f"[ \"$1\" = --version ] && echo 'pores {PORES_VERSION}' || exit 1"),
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as client:  # started: the versions are read
        yield client
