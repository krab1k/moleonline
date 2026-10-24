"""Mole_api_IP_log.csv, next to the computations (it outlives the API): one line per load or submission request, also
a refused one (not one the framework refused as invalid, before the endpoint): time (UTC, as in the status files),
Init or Submit, "-", client address, PDB/Upload/Mole/Pores, session id ("-" when no computation was created).
tools/stats.py counts users from it. Usage statistics only: a line that cannot be written is logged, the request is
answered as without the log."""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

FILE_NAME = "Mole_api_IP_log.csv"

log = logging.getLogger("uvicorn.error")  # the API's log, in uvicorn's format, one line per event

# the client address is the reverse proxy's X-Forwarded-For, from the proxies of uvicorn's FORWARDED_ALLOW_IPS (Dockerfile)


def write(working_directory: Path, request: str, address: str | None, kind: str, computation_id: str | None) -> None:
    # ids are letters and digits; one from the request may be anything, one request stays one line
    id = "-" if computation_id is None else re.sub("[^A-Za-z0-9]", "?", computation_id[:40])
    line = f"{datetime.now(UTC):%Y-%m-%d %H:%M:%S} {request} - {address} {kind} {id}\n"
    try:
        with (working_directory / FILE_NAME).open("a") as file:  # one write of one line, appends do not mix
            file.write(line)
    except OSError as e:
        log.warning("The request log could not be written: %s", e)
