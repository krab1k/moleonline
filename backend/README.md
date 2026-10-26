# MOLEonline backend

The backend loads structures, computes cavities, tunnels and pores with [MOLE 2](https://github.com/sb-ncbr/webchemistry)
and serves the results to the website.

It has two parts, both in `src/moleonline`:

- **The API** (`api`, FastAPI) answers requests from the website and puts computations into a queue.
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
oldest waiting computation. To run more at once, add workers: `docker compose up -d --scale worker=N`. The API keeps
nothing in memory, so it can run as several replicas, too.

Restarting the API or the workers is safe. Waiting computations stay in the queue. A computation that was running fails
with "interrupted by a restart", and the user can submit it again. The same happens when a worker crashes: if it stops
reporting for `WORKER_TIMEOUT_SECONDS`, the API marks its computation as interrupted.

### Data

The database holds the sessions, their jobs, the queue and a log of submitted requests. Files are in the `data` volume:

| Path | Contents |
|---|---|
| `/data/computations/<id>/` | One session: its structure, `load/` and `jobs/<n>/` with MOLE's input and results |
| `/data/opm-cache/` | Membranes downloaded from OPM, kept for 30 days |

Monthly or yearly usage (sessions, jobs and their outcomes, distinct users):

```sh
docker compose exec api /app/.venv/bin/python -m moleonline.api.stats month
```

## Configuration

All settings are environment variables. The defaults suit the containers (see `src/moleonline/core/config.py`).

For both the API and the workers:

| Setting | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | (required) | `postgresql+asyncpg://user:password@host/database` |
| `WORKING_DIRECTORY` | `/data/computations` | Where session files are stored |
| `WORKER_TIMEOUT_SECONDS` | `60` | How long a silent worker is waited for before its computation fails |

For the workers:

| Setting | Default | Meaning |
|---|---|---|
| `MAX_COMPUTATION_MINUTES` | `30` | Time limit of one computation (a ribosome takes about 2 minutes) |
| `MOLE` | `/opt/mole2/mole2` | Path to MOLE |
| `MEMEMBED` | `/opt/memembed/memembed` | Path to MEMEMBED |
| `OPM_CACHE` | `/data/opm-cache` | Where membranes from OPM are cached |
| `HEARTBEAT_FILE` | `/tmp/heartbeat` | Touched every few seconds; a liveness probe checks its age |

For the API:

| Setting | Default | Meaning |
|---|---|---|
| `MAX_OUTSTANDING_COMPUTATIONS` | `24` | Computations waiting or running at most; more are refused with 503 |
| `FORWARDED_ALLOW_IPS` | private networks | Proxies trusted to report the client's address (uvicorn's setting) |
| `UVICORN_ROOT_PATH` | (none) | Path the API is served under, `/api` behind the website's nginx |

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
