"""The request log (the table requests): one row per request creating a session or a job, with the client's address"""

import asyncio
import os
import subprocess
import sys

from moleonline import storage

from .conftest import STRUCTURE, query, upload, wait_session


def logged(settings) -> list[tuple[str, ...]]:
    """request, address, kind, session id (- when none was created) of each request, in order"""
    rows = query("SELECT request, address, kind, session_id FROM requests ORDER BY id")
    return [(request, address, kind, session_id or "-") for request, address, kind, session_id in rows]


def test_loads_are_logged(client, settings):
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    upload(client, "empty.pdb", b"")  # refused, no computation
    assert logged(settings) == [("Init", "testclient", "Upload", id), ("Init", "testclient", "Upload", "-")]


def test_requests_are_answered_without_the_log(client, caplog):
    query("DROP TABLE requests")  # cannot be written
    assert upload(client, "1tqn.cif", STRUCTURE).status_code == 202
    assert any("request could not be logged" in r.getMessage() for r in caplog.records)


def test_id_from_the_request_is_letters_and_digits(database):
    async def log() -> None:
        db = storage.Database(database)
        await db.migrate()
        await storage.log_request(db, "Submit", "203.0.113.7", "Mole", "x\ny z/" + "a" * 50)
        await db.close()
    asyncio.run(log())
    assert query("SELECT session_id FROM requests") == [("x?y?z?" + "a" * 34,)]


def test_stats(client, settings):
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    wait_session(client, id)
    output = subprocess.run([sys.executable, "-m", "moleonline.stats", "year"], env=os.environ | {"DATABASE_URL": settings.database_url},
                            capture_output=True, text=True, check=True).stdout
    header, row = [line.split("\t") for line in output.splitlines()]
    values = dict(zip(header, row, strict=True))
    assert values["sessions"] == "1" and values["uploads"] == "1" and values["users"] == "1"
