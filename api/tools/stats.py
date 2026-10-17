"""Usage of MOLEonline per month or year, from the computations folder (WORKING_DIRECTORY).

usage: python3 stats.py <computations folder> [month|year]

In the container (with the API's Python):
    docker compose exec -T api /app/.venv/bin/python -I - /data/computations year < tools/stats.py

All times are UTC, in the status files and in the log, so both are grouped by the same calendar.
- computations: by Created in _object.json, split into PDB entries and uploads; status files written before the
  times were recorded (no Created) are skipped
- submissions: units with parameters (mole.json: tunnels, pores.json: pores) by Submitted, with their current
  status: finished, failed, aborted, running, or deleted (deleting a computation replaces the status of its last
  submission, whose outcome is then unknown)
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
for status_file in root.glob("*/_object.json"):
    try:
        computation = json.loads(status_file.read_text())
    except (OSError, ValueError):
        continue
    if not computation.get("Created"):
        continue  # written before the times were recorded
    row = counts[computation["Created"][:length]]
    row["computations"] += 1
    row["uploads" if computation.get("UserStructure") else "PDB entries"] += 1
    for unit in computation["ComputationUnits"]:
        folder = status_file.parent / str(unit["SubmitId"])
        kind = "tunnels" if (folder / "mole.json").exists() else "pores" if (folder / "pores.json").exists() else None
        if kind is None:
            continue  # the load's unit, never used for a submission
        row = counts[(unit.get("Submitted") or computation["Created"])[:length]]
        row["submissions"] += 1
        row[kind] += 1
        row["failed" if unit["Status"] in ("Error", "FailedInitialization") else unit["Status"].lower()] += 1

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

columns = ["computations", "PDB entries", "uploads", "submissions", "tunnels", "pores",
           "finished", "failed", "aborted", "running", "deleted", "users", "submitters"]
print("\t".join(["period"] + columns))
for period in sorted(set(counts) | set(users)):
    row = counts[period]
    row["users"], row["submitters"] = len(users[period]), len(submitters[period])
    print("\t".join([period] + [str(row[c]) for c in columns]))
