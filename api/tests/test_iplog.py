"""Mole_api_IP_log.csv: one line per load or submission request, with the client's address"""

import re
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from mole_api import iplog
from mole_api.main import create_app

from .conftest import STRUCTURE, make_settings, upload, wait

LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d (Init|Submit) - (\S+) (\S+) (\S+)$")


def logged(settings) -> list[tuple[str, ...]]:
    """request, address, kind, id of each line"""
    lines = (settings.working_directory / iplog.FILE_NAME).read_text().splitlines()
    return [LINE.match(line).groups() for line in lines]


def test_loads_are_logged(client, settings):
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    upload(client, "empty.pdb", b"")  # refused, no computation
    assert logged(settings) == [("Init", "testclient", "Upload", id), ("Init", "testclient", "Upload", "-")]


def test_client_address_from_the_proxy(tmp_path):
    settings = make_settings(tmp_path)
    for proxy, logged_address in [("172.18.0.5", "203.0.113.7"), ("198.51.100.1", "198.51.100.1")]:  # Docker network; public: not trusted
        with TestClient(create_app(settings), client=(proxy, 40000)) as client:
            client.post("/Init", files={"file": ("1tqn.txt", b"x")}, headers={"X-Forwarded-For": "203.0.113.7"})
        assert logged(settings)[-1][1] == logged_address


def test_id_from_the_request_stays_one_line(tmp_path):
    iplog.write(tmp_path, "Submit", "203.0.113.7", "Mole", "x\ny z/" + "a" * 50)
    assert (tmp_path / iplog.FILE_NAME).read_text().endswith(" Submit - 203.0.113.7 Mole x?y?z?" + "a" * 34 + "\n")


def test_stats_read_the_log(client, settings):
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    wait(client, id)
    stats = Path(__file__).parent.parent / "tools" / "stats.py"
    output = subprocess.run([sys.executable, "-I", stats, settings.working_directory, "year"], capture_output=True, text=True, check=True).stdout
    header, row = [line.split("\t") for line in output.splitlines()]
    values = dict(zip(header, row))
    assert values["computations"] == "1" and values["uploads"] == "1" and values["users"] == "1"
