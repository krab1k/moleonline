"""A worker process: python -m moleonline.worker. Runs one queued run at a time until SIGTERM or SIGINT (a deploy,
maintenance), which kills the running run's processes; its lease expires and it fails as interrupted. A failure while
a run runs (the database) ends the process with exit code 1, to be restarted."""

import asyncio
import logging
import signal
import sys

from uvicorn.logging import DefaultFormatter

from ..core import storage
from ..core.config import WorkerSettings
from . import pdbe
from .runner import Worker
from .versions import read_mole_version

log = logging.getLogger("uvicorn.error")


async def main() -> None:
    settings = WorkerSettings()  # a wrong setting stops the start
    mole_version = await read_mole_version(settings)
    db = storage.Database(settings.database_url)
    await db.migrate()
    http = pdbe.client()
    worker = Worker(settings, db, lambda: http, mole_version)
    running = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for stop in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop, running.cancel)
    log.info("Worker %s with MOLE %s runs the queued runs", worker.name, mole_version)
    try:
        await worker.run()
    except asyncio.CancelledError:
        log.info("Worker %s stopped", worker.name)
    finally:
        await http.aclose()
        await db.close()


if __name__ == "__main__":
    handler = logging.StreamHandler()
    handler.setFormatter(DefaultFormatter("%(levelprefix)s %(message)s"))  # as the API's log
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    try:
        asyncio.run(main())
    except Exception:
        log.exception("The worker stops")
        sys.exit(1)
