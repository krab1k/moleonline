# MOLEonline backend

The backend loads structures, computes cavities, tunnels and pores with [MOLE 2](https://github.com/sb-ncbr/webchemistry)
and serves the results to the website.

It has two parts, both in `src/moleonline`:

- **The API** (`api`, FastAPI) answers requests from the website and puts computations into a queue. It also serves
  an [MCP server](#mcp-server) for AI assistants.
- **The workers** (`worker`) take computations from the queue and run MOLE, one at a time each. For pores through the
  membrane, they first place the membrane, using [OPM](https://opm.phar.umich.edu/) or a prediction by
  [MEMEMBED](https://github.com/psipred/MemEmbed).

Code shared by both is in `core`. The queue, sessions and jobs are stored in PostgreSQL.

The [`Dockerfile`](Dockerfile) builds two images: `--target api` with Python only, and `--target worker`, which adds MOLE
2 (on the .NET runtime) and MEMEMBED. The MOLE version comes from the build argument `MOLE_VERSION`.

## Running

The backend runs together with the website, either with the repository's [`compose.yaml`](../compose.yaml) (see the
[main README](../README.md#running-locally)) or in Kubernetes. The website's nginx serves the API under `/api`.

To follow the logs:

```sh
docker compose logs -f api worker
```

If a setting is wrong (a path that does not exist, for example), the container stops right away and the log says why.

### How computations run

When you load a structure or submit a job, the API adds it to the queue and answers immediately. A free worker picks the
website's computations before those of scripts (see below), and of them the oldest waiting computation of the client with
the fewest running ones, so a client with many queued computations only uses the workers nobody else needs. A client is an IP address; IPv6 addresses count by their /64 network. To run more
at once, add workers: `docker compose up -d --scale worker=N`. The API keeps nothing in memory, so it can run as several
replicas, too.

When the queue is full, new computations are refused with 503. A single client is refused with 429 earlier: when it
has `MAX_OUTSTANDING_PER_CLIENT` computations waiting or running, or started `MAX_COMPUTATIONS_PER_HOUR` in the last
hour. Both answers say when to try again (`Retry-After`). People behind one shared address (a course in one room)
count as one client; raise the limits for such a day.

The website's computations and those of scripts are counted apart, so that scripts and AI assistants never take the
website from its users. A request is from the website when it has the site's own `Origin`, which browsers add to the
website's requests; scripts, assistants (also through the [MCP server](#mcp-server)) and other sites have none. Their
computations are *programmatic*:

- All of them together take at most `MAX_PROGRAMMATIC_COMPUTATIONS` places in the queue (half of it) and start at most
  `MAX_PROGRAMMATIC_PER_HOUR` in an hour; more are refused with 503.
- Each of their clients has lower limits than a website's user: `PROGRAMMATIC_PER_CLIENT` at once and
  `PROGRAMMATIC_PER_CLIENT_PER_HOUR` in an hour (429).
- Workers take the website's computations first, and run at most `MAX_RUNNING_PROGRAMMATIC` programmatic ones at once
  (about half of the workers), so their long runs never hold all of them.

This sorts the traffic, it does not prove anything: a script sending the site's `Origin` counts as a website's user,
with the limits of its address. The usage statistics count the programmatic requests.

Restarting the API or the workers is safe. Waiting computations stay in the queue. A computation that was running fails
with "interrupted by a restart", and the user can submit it again. The same happens when a worker crashes: if it stops
reporting for `WORKER_TIMEOUT_SECONDS`, the API marks its computation as interrupted.

### Data

The database holds the sessions, their jobs, the queue and a log of submitted requests. Files are in the `data` volume:

| Path | Contents |
|---|---|
| `/data/computations/<id>/` | One session: its structure, `data.json` (what its load found) and `jobs/<n>/` with each finished job's results |
| `/data/opm-cache/` | Membranes downloaded from OPM, kept for 30 days |

A job's folder holds `data.json` (what the website shows), `report.zip` (all of MOLE's results and the structure) and,
for pores, `membrane.json`. The other downloads are read from `report.zip`. The workers run MOLE in a folder on their
own disk and copy only these files to the volume, because MOLE writes many small files and a shared volume (NFS) is
slow with those.

Monthly or yearly usage (sessions, jobs and their outcomes, distinct users):

```sh
docker compose exec api /app/.venv/bin/python -m moleonline.api.stats month
```

## Configuration

All settings are environment variables. The defaults suit the containers (see `src/moleonline/core/config.py`).

For both the API and the workers:

| Setting | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | (required) | `postgresql://user:password@host/database` |
| `WORKING_DIRECTORY` | `/data/computations` | Where session files are stored |
| `WORKER_TIMEOUT_SECONDS` | `60` | How long a silent worker is waited for before its computation fails |

For the workers:

| Setting | Default | Meaning |
|---|---|---|
| `MAX_COMPUTATION_MINUTES` | `30` | Time limit of one computation (a ribosome takes about 2 minutes) |
| `MOLE` | `/opt/mole2/mole2` | Path to MOLE |
| `MEMEMBED` | `/opt/memembed/memembed` | Path to MEMEMBED |
| `OPM_CACHE` | `/data/opm-cache` | Where membranes from OPM are cached |
| `SCRATCH_DIRECTORY` | `/tmp/moleonline` | Where computations run, on a local disk; each worker needs its own, it is emptied when the worker starts |
| `HEARTBEAT_FILE` | `/tmp/heartbeat` | Touched every few seconds; a liveness probe checks its age |
| `MAX_RUNNING_PROGRAMMATIC` | `2` | Computations of scripts and assistants running at once on all the workers, about half of them; the website's computations go first |

For the API:

| Setting | Default | Meaning |
|---|---|---|
| `MAX_OUTSTANDING_COMPUTATIONS` | `24` | Computations waiting or running at most; more are refused with 503 |
| `MAX_OUTSTANDING_PER_CLIENT` | half of the above | Of them, from one client; more are refused with 429 |
| `MAX_COMPUTATIONS_PER_HOUR` | `200` | Computations one client may start in an hour; more are refused with 429 |
| `MAX_PROGRAMMATIC_COMPUTATIONS` | half of `MAX_OUTSTANDING_COMPUTATIONS` | Computations of scripts and assistants (not from the website) waiting or running, all together; more are refused with 503 |
| `MAX_PROGRAMMATIC_PER_HOUR` | `200` | Computations of scripts and assistants started in an hour, all together; more are refused with 503 |
| `PROGRAMMATIC_PER_CLIENT` | `4` | Computations of one script's or assistant's client waiting or running; more are refused with 429 |
| `PROGRAMMATIC_PER_CLIENT_PER_HOUR` | `50` | Computations one script's or assistant's client may start in an hour; more are refused with 429 |
| `FORWARDED_ALLOW_IPS` | private networks | Proxies trusted to report the client's address (uvicorn's setting) |
| `UVICORN_ROOT_PATH` | (none) | Path the API is served under, `/api` behind the website's nginx |
| `PUBLIC_URL` | (from the request) | The site's address as users see it, e.g. `https://moleonline.biodata.ceitec.cz`, for links in the MCP server's answers |

Each worker gets `WORKER_MEMORY_LIMIT` of memory (6 GB by default in compose), which is enough for a ribosome. If a
computation needs more, it fails, usually with an out-of-memory error. The API itself needs little, 2 GB is plenty.

## API

Interactive documentation is at `/api/docs` on the running site, and the OpenAPI description in
[`openapi.json`](openapi.json).

| Endpoint | Description |
|---|---|
| `POST /sessions` | Load a PDB entry from PDBe, e.g. `{"pdb_id": "1tqn"}` |
| `POST /sessions/upload` | Load an uploaded structure (`.pdb`, `.cif`, optionally gzipped, up to 30 MB) |
| `GET /sessions/{id}` | State of a session: `queued`, `loading`, `ready` or `failed` |
| `POST /sessions/{id}/cancel` | Stop loading a structure |
| `DELETE /sessions/{id}` | Delete a session |
| `POST /sessions/{id}/jobs` | Compute tunnels (`"type": "tunnels"`) or a pore (`"type": "pores"`) |
| `GET /sessions/{id}/jobs/{n}` | State of a job: `queued`, `running`, `finished`, `failed` or `cancelled` |
| `POST /sessions/{id}/jobs/{n}/cancel` | Stop a job |
| `GET /sessions/{id}/jobs/{n}/results/{format}` | Results: `data`, `report`, `pymol`, `chimera`, `vmd`, `pdb` or `membrane` |
| `GET /sessions/{id}/structure` | The loaded structure |
| `GET /sessions/{id}/data` | Cavities found when the structure was loaded |
| `GET /sessions/{id}/active-sites` | Active sites from M-CSA, as starting points for tunnels |
| `GET /cofactors` | Cofactors, as starting points for tunnels |
| `GET /version` | Versions of the API and MOLE |

Errors are returned as [problem details](https://www.rfc-editor.org/rfc/rfc9457) with a `detail` message for the user.
A computation that fails is not an HTTP error: its `state` is `failed` and `error` says why.

The active sites come from [M-CSA](https://www.ebi.ac.uk/thornton-srv/m-csa/) (Ribeiro et al., Nucleic Acids Research
2018, CC BY 4.0). `tools/mcsa/convert.py` converts them into `src/moleonline/api/resources/mcsa_sites.csv.gz`.

## MCP server

AI assistants (Claude, Cursor and others) can use MOLEonline through the
[Model Context Protocol](https://modelcontextprotocol.io). The server is at `/mcp` on the site, for example
`https://moleonline.biodata.ceitec.cz/mcp` (`http://localhost:8080/mcp` locally). In Claude Code:

```sh
claude mcp add --transport http moleonline https://moleonline.biodata.ceitec.cz/mcp
```

In Claude's apps, add it as a custom connector with the same URL.

| Tool | Description |
|---|---|
| `load_structure` | Load a PDB entry |
| `load_structure_url` | Load a structure file from a public https URL, e.g. an AlphaFold or AlphaFill model (up to 30 MB, downloaded within 30 s; PDB or mmCIF by the extension, or by the content when the URL has none) |
| `get_session` | State of a session and its jobs |
| `compute_tunnels` | Compute tunnels from the M-CSA active site, given residues, ligands or cofactors (e.g. `["HEM"]`), a point [x, y, z] or automatically found starting points |
| `compute_pore` | Compute a pore through the membrane |
| `get_job` | State of a job and what it computes with (structure, starting point, parameters, MOLE's version); when finished, summaries of its channels in MOLE's order, 20 at a time (`offset`, `next_offset`): length, bottleneck (the layer MOLE flags) and narrowest point, properties |
| `get_channel` | One channel of a finished job: its summary, lining and bottleneck residues, each with its parts (name, number, chain, symmetry copy, insertion code) and whether only its backbone lines the channel |

`compute_tunnels` and `get_job` say where the tunnels start (`starting_point`), in the same form as ChannelsDB's MCP
server, e.g. `[{"start": "residues", "residues": ["A 309", "A 482"]}]`; for `active_site`, the residues of the first site
M-CSA knows. Reading tools are marked read-only, so clients may run them without asking each time. The answers also link
to the result on the website and to the downloads. MOLE's full `data.json` is available as the
resource `mole://sessions/{id}/jobs/{n}/data`, with all the channels.

The tools call the API's endpoints inside the API process, so the same validation applies as for the REST API. Their
computations are programmatic, with the limits of [scripts](#how-computations-run), and count towards the user's own
address. The server keeps no state between requests and answers with
plain JSON, so any API replica can answer it. The tools wait at most 45 seconds for a structure or a computation (a
download included); if it is still loading or running, the assistant asks again.

## Development

Install the dependencies and run the API and a worker:

```sh
uv sync
export DATABASE_URL=postgresql+asyncpg://user:password@localhost/mole WORKING_DIRECTORY=/tmp/mole
uv run uvicorn moleonline.api.main:app
OPM_CACHE=/tmp/opm-cache MOLE=/path/to/mole2 MEMEMBED=/path/to/memembed uv run python -m moleonline.worker
```

### Tests

The tests need an empty PostgreSQL database. A throwaway container is easiest:

```sh
docker run -d --name moleonline-test-db -p 127.0.0.1:55432:5432 -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=test --tmpfs /var/lib/postgresql docker.io/library/postgres:18-alpine -c fsync=off
export TEST_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/test
uv run pytest
uv run ruff check
```

The tests use a fake MOLE and fake external services. Tests that need the real MOLE or MEMEMBED run only when you point
`MOLE2` and `MEMEMBED` at them.

### Database schema

The tables are defined in `src/moleonline/core/storage.py` and created by an
[Alembic](https://alembic.sqlalchemy.org/) migration in `src/moleonline/core/migrations/versions`. The API and the
workers apply it when they start.

Until the first real deployment, there is a single migration that is regenerated whenever the tables change (a test
fails when it is out of date). Generate it from an empty database:

```sh
docker exec moleonline-test-db psql -U test -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public'
rm src/moleonline/core/migrations/versions/*.py
DATABASE_URL=$TEST_DATABASE_URL uv run alembic revision --autogenerate --rev-id initial -m tables
```

Existing local databases must then be recreated with `docker compose down -v`.

### OpenAPI

The website generates its types from `openapi.json`. After changing the API, update it with
`uv run python tools/openapi.py`, then run `npm run api-types` in `web/`. A test fails while it is out of date.

### The pores tool

The pores tool (`src/moleonline/worker/pores`) runs as `python -m moleonline.worker.pores <pores.json>`. Its input is
described in `cli.py`. Its tests compare results with those of the original Pores tool, stored in `tests/pores/data/`.

## Licence

MIT, see [`LICENSE`](../LICENSE). MEMEMBED, included in the worker image, is GPL v2; its source and licence are in
`/opt/memembed` in the image.
