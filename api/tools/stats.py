"""Usage of MOLEonline per month or year, from the sessions folder (WORKING_DIRECTORY).

usage: python3 stats.py <sessions folder> [month|year]

In the container (with the API's Python):
    docker compose exec -T api /app/.venv/bin/python -I - /data/computations year < api/tools/stats.py

All times are UTC, in the status files and in the log, so both are grouped by the same calendar.
- sessions: by created in session.json, split into PDB entries and uploads, and those deleted since
- jobs: by submitted, split into tunnels and pores, with their state: finished, failed, cancelled, or in progress
  (queued or running)
- users: distinct client addresses that loaded or submitted (Mole_api_IP_log.csv, refused requests included);
  submitters: those that submitted. One address is not one person (shared or changing addresses).
Only the standard library is used.
"""

import collections
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
length = 4 if len(sys.argv) > 2 and sys.argv[2] == "year" else 7  # "2026" or "2026-10"

counts = collections.defaultdict(collections.Counter)
for status_file in root.glob("*/session.json"):
    try:
        session = json.loads(status_file.read_text())
    except (OSError, ValueError):
        continue
    row = counts[session["created"][:length]]
    row["sessions"] += 1
    row["uploads" if session["pdb_id"] is None else "PDB entries"] += 1
    row["deleted"] += session["deleted"]
    for job in session["jobs"]:
        row = counts[job["submitted"][:length]]
        row["jobs"] += 1
        row[job["type"]] += 1
        row["in progress" if job["state"] in ("queued", "running") else job["state"]] += 1

users = collections.defaultdict(set)
submitters = collections.defaultdict(set)
log = root / "Mole_api_IP_log.csv"
if log.exists():
    for line in log.read_text().splitlines():
        fields = line.split(" ")  # date time Init|Submit - address kind id
        if len(fields) >= 5:
            period = fields[0][:length]
            users[period].add(fields[4])
            if fields[2] == "Submit":
                submitters[period].add(fields[4])

columns = ["sessions", "PDB entries", "uploads", "deleted", "jobs", "tunnels", "pores",
           "finished", "failed", "cancelled", "in progress", "users", "submitters"]
print("\t".join(["period", *columns]))
for period in sorted(set(counts) | set(users)):
    row = counts[period]
    row["users"], row["submitters"] = len(users[period]), len(submitters[period])
    print("\t".join([period] + [str(row[c]) for c in columns]))
