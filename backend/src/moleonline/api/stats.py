"""Usage of MOLEonline per month or year: python -m moleonline.api.stats [month|year], with the API's DATABASE_URL. In the
container:

    docker compose exec api /app/.venv/bin/python -m moleonline.api.stats year

All times are UTC, so the sessions, the jobs and the request log are grouped by the same calendar.
- sessions: by created, split into PDB entries and uploads, and those deleted since
- jobs: by submitted, split into tunnels and pores, with their state: finished, failed, cancelled, or in progress
  (queued or running)
- users: distinct client addresses that loaded or submitted (the requests, refused ones included); submitters: those
  that submitted. One address is not one person (shared or changing addresses).
"""

import asyncio
import collections
import os
import sys

from sqlalchemy import select

from ..core import storage

COLUMNS = ["sessions", "PDB entries", "uploads", "deleted", "jobs", "tunnels", "pores",
           "finished", "failed", "cancelled", "in progress", "users", "submitters"]


async def usage(db: storage.Database, length: int) -> dict[str, collections.Counter]:
    """The counts of each period: length 4 (a year, 2026) or 7 (a month, 2026-10)"""
    counts = collections.defaultdict(collections.Counter)
    async with db.engine.connect() as c:
        sessions, jobs = storage.sessions.c, storage.jobs.c
        for created, pdb_id, deleted in await c.execute(select(sessions.created, sessions.pdb_id, sessions.deleted)):
            row = counts[created.isoformat()[:length]]
            row["sessions"] += 1
            row["uploads" if pdb_id is None else "PDB entries"] += 1
            row["deleted"] += deleted
        for submitted, type, state in await c.execute(select(jobs.submitted, jobs.type, jobs.state)):
            row = counts[submitted.isoformat()[:length]]
            row["jobs"] += 1
            row[type] += 1
            row["in progress" if state in storage.JOB_IN_PROGRESS else state] += 1
        users, submitters = collections.defaultdict(set), collections.defaultdict(set)
        requests = storage.requests.c
        for time, request, address in await c.execute(select(requests.time, requests.request, requests.address)):
            period = time.isoformat()[:length]
            users[period].add(address)
            if request == "Submit":
                submitters[period].add(address)
    for period in set(users):
        counts[period]["users"], counts[period]["submitters"] = len(users[period]), len(submitters[period])
    return dict(sorted(counts.items()))


async def main() -> None:
    db = storage.Database(os.environ["DATABASE_URL"])
    try:
        counts = await usage(db, 4 if sys.argv[1:] == ["year"] else 7)
    finally:
        await db.close()
    print("\t".join(["period", *COLUMNS]))
    for period, row in counts.items():
        print("\t".join([period] + [str(row[c]) for c in COLUMNS]))


if __name__ == "__main__":
    asyncio.run(main())
