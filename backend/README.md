# MOLEonline API

Backend of [MOLEonline](../web): loads protein structures, computes
cavities, tunnels and pores with [MOLE 2](https://github.com/sb-ncbr/webchemistry) and serves the results.

The Python code (`src/moleonline`) is the API (`api`, FastAPI), the workers, which run the computations (`worker`), and
what both share (`core`); they keep the sessions, their jobs and the queue of their runs in PostgreSQL. The
[`Dockerfile`](Dockerfile) builds two images of it:

- `--target api`, the API: Python only;
- `--target worker`, a worker, with
  - the pores tool (`src/moleonline/worker/pores`, run as a process of its own): the membrane position for pore
    computations, from [OPM](https://opm.phar.umich.edu/) or predicted by MEMEMBED,
  - MOLE 2, from the image `sbncbr/mole2` (the build argument `MOLE_VERSION` picks it), on the .NET runtime,
  - [MEMEMBED](https://github.com/psipred/MemEmbed), built from source (GPL v2, its source and licence are in
    `/opt/memembed` in the image).

## Running

MOLEonline runs as a whole, the web with the backend: all of it with the repository's [`compose.yaml`](../compose.yaml)
(see the [README](../README.md#running)), or all of it in Kubernetes. Either way the web's nginx, or the gateway in
front of it, serves the API under `/api` on the site's own address. The settings (see [Configuration](#configuration))
are checked when the API and the workers start: if a path is wrong, the container stops and its log says what is wrong.
In compose they go into `.env` next to `compose.yaml`; the logs, one line per event (requests are not logged, errors
with the request path):

```sh
docker compose logs -f api worker
```

The API (`uvicorn moleonline.api.main:app`, its image's entrypoint) answers the requests and queues the runs: a
session's load, a job. The workers (`python -m moleonline.worker`, theirs) run them, one at a time each, the
oldest first; more of them, `docker compose up -d --scale worker=N`. Both keep nothing in memory: the API may run as
several replicas too. They share the database and the volume `data` (in Kubernetes ReadWriteMany, e.g. NFS).

### Behind a proxy

The API takes the client address (the request log, see below) from `X-Forwarded-For` of the proxies in
`FORWARDED_ALLOW_IPS` (uvicorn's), by default any on loopback or a private network, where the web's nginx or the
gateway has an address not known in advance. The proxy serves the API under `/api/`, strips it, and the API runs with
`UVICORN_ROOT_PATH=/api`, so that its documentation (`/api/docs`) and "Try it out" use the site's URLs (the web's
nginx: `web/docker/site.conf`).

### Data

The sessions, their jobs and the queue of their runs are in the database (`DATABASE_URL`), with the request log
(table `requests`): every request creating a session or a job, also refused ones (except those invalid in form, refused
before the endpoint), best-effort, with its time, `Init` (a session) or `Submit` (a job), the client's address, `PDB`,
`Upload`, `Mole` (tunnels) or `Pores`, and the session's id (none when none was created). Every change of a state is one
transaction that applies only from the state it expects, so a cancel or a delete is never overwritten by a run's late
outcome. The files are in the volume `data`, mounted at `/data`:

| Path | Content |
|---|---|
| `/data/computations/<id>/` | one session's files: the structure, `load/` (the load's files), `jobs/<n>/` (a job's files) |
| `/data/opm-cache/` | membrane files downloaded from OPM, reused for 30 days |

Usage per month or year (sessions of PDB entries and uploads, jobs and their outcome, distinct client addresses), from
the database:

```sh
docker compose exec api /app/.venv/bin/python -m moleonline.api.stats month
```

A worker running a run renews its lease twice a second, and stops the run when it no longer holds it: cancelled,
deleted, or expired. A lease not renewed for `WORKER_TIMEOUT_SECONDS` expires: its worker was stopped (a deploy, SIGTERM
kills its run's processes), crashed or lost its node, or the database failed while it ran (the worker then kills the
run and exits, to be restarted). The API fails such a run as interrupted ("interrupted by a restart"), the user can
submit it again; it is never run again by itself. Queued runs wait over restarts of the API and the workers. Restarting
either is always possible (`docker compose restart`, `kubectl rollout restart`): queued work is kept, running work is
interrupted. A run records the MOLE of the worker that ran it, `/version` the latest one's.

## Configuration

Settings are environment variables, the defaults are the container's (`src/moleonline/core/config.py`). Of the API
and the workers:

| Setting | Default | |
|---|---|---|
| `WORKING_DIRECTORY` | `/data/computations` | the sessions' files, must be writable |
| `DATABASE_URL` | none, required | `postgresql+asyncpg://user:password@host/database`; the API and the workers wait for it when they start, then bring its tables to their code's (see [Database](#database)) |
| `WORKER_TIMEOUT_SECONDS` | `60` | a run whose worker did not renew its lease this long fails as interrupted |

Of the workers:

| Setting | Default | |
|---|---|---|
| `MAX_COMPUTATION_MINUTES` | `30` | a run taking longer is stopped and reported as an error (a ribosome takes about 2 minutes) |
| `MOLE` | `/opt/mole2/mole2` | full path; its version is read when the worker starts |
| `MEMEMBED` | `/opt/memembed/memembed` | full path, the membrane of pore computations without OPM's |
| `OPM_CACHE` | `/data/opm-cache` | membrane files downloaded from OPM, must be writable |
| `HEARTBEAT_FILE` | `/tmp/heartbeat` | touched by the worker's loop at least every 2 s, also while it waits for the database; older, the worker hangs (compose's healthcheck, a liveness probe) |

Of the API:

| Setting | Default | |
|---|---|---|
| `MAX_OUTSTANDING_COMPUTATIONS` | `24` | runs (loads, jobs) queued or running together; a new session or job beyond them is refused (503 with `Retry-After`) |
| `CORS_ORIGINS` | `["*"]` | sites whose pages may call the API, a JSON list, e.g. `["https://moleonline.cz"]` |
| `FORWARDED_ALLOW_IPS` | private networks | reverse proxies whose `X-Forwarded-For` gives the client's address (the request log), uvicorn's setting: addresses and networks separated by commas; `127.0.0.1` when clients connect directly, so that none can choose its address |
| `UVICORN_ROOT_PATH` | none | the path prefix of a proxy serving the API under it, e.g. `/api` (uvicorn's `--root-path`) |

All times the API stores are in UTC.

A worker's memory, its one computation's, is limited to `WORKER_MEMORY_LIMIT` (compose, default `6g`, in `.env` next to
`compose.yaml`; in Kubernetes the pod's limit); the API's container has 2 GB. Near the limit MOLE (.NET) stops with an
out of memory error; above it the kernel kills the computation, reported as having run out of memory, or the whole
worker, whose run then fails as interrupted. A computation shares its limit with no other.

## Endpoints

The OpenAPI documentation is at `/docs` (`/openapi.json`). Responses are JSON, except the files to download. A
request that fails has an HTTP error status and problem details ([RFC 9457](https://www.rfc-editor.org/rfc/rfc9457),
`application/problem+json`): `type` (`about:blank`), `title` (the status' phrase), `status` and `detail` (why), with
`errors` (`field`, `message`) for a request that failed validation. 404 no such session, job or data, 409 a job runs on
the session or its structure could not be loaded, 410 deleted, 413 upload too large, 422 invalid request (body, parameters,
PDB id, file type, data format), 503 the queue is full (`Retry-After`), 500 server error. A session or job that failed is a successful
request, 200, with its `state` and `error`.

| Endpoint | |
|---|---|
| `POST /sessions` | a session of a PDB entry, downloaded from PDBe, its cavities computed; body `{"pdb_id": "1tqn", "assembly": "preferred"}`, `assembly`: `preferred` (default, PDBe's preferred biological assembly, the one OPM has; the asymmetric unit when PDBe cannot tell which one it is, e.g. when it is not reachable), `asymmetric` (the asymmetric unit) or an assembly id; `202 Accepted` with `Location`, the session |
| `POST /sessions/upload` | a session of an uploaded structure (`multipart/form-data`, `.pdb`, `.cif`, `.pdb.gz`, `.cif.gz`); a gzipped file is unpacked and stored as `.pdb` or `.cif`; `202 Accepted` with `Location` |
| `GET /sessions/{id}` | the session: `state` `queued`, `loading`, then `ready` or `failed` (`error` says why), the PDB entry and assembly, times |
| `POST /sessions/{id}/cancel` | stop loading the structure (the session fails); only a queued or loading session |
| `DELETE /sessions/{id}` | delete the session, `204 No Content`; `410 Gone` from then on |
| `POST /sessions/{id}/jobs` | a job on the session's structure: `{"type": "tunnels", "parameters": <MOLE parameters>}`, or `{"type": "pores", "parameters": {"InMembrane", "IsBetaBarrel", "Chains" (list of chain names, empty: all), "ProbeRadius", "InteriorThreshold" (null: the default)}}` for a pore across the membrane; `202 Accepted` with `Location`; one job runs on a session at a time |
| `GET /sessions/{id}/jobs/{n}` | the job: `state` `queued`, `running`, then `finished`, `failed` (`error` says why) or `cancelled`, its parameters and times; jobs are numbered from 1, `GET /sessions/{id}` lists them all |
| `POST /sessions/{id}/jobs/{n}/cancel` | stop the job (cancelled); only a queued or running job |
| `GET /sessions/{id}/jobs/{n}/results/{format}` | a finished job's results: `data` (MOLE's `data.json`, what the web shows), `report` (a zip of all), `pymol`, `chimera`, `vmd` (scripts of the viewers), `pdb` (a zip of the tunnels' profiles), `membrane` (of a pore job, also when no pore was found) |
| `GET /sessions/{id}/structure` | the session's structure, as loaded (a gzipped upload unpacked) |
| `GET /sessions/{id}/data` | what the load found, MOLE's `data.json`: the cavities and the origins |
| `GET /sessions/{id}/active-sites` | catalytic active sites of the PDB entry from M-CSA, one per copy, starting points for tunnels |
| `GET /cofactors` | cofactors by residue name, with the PatternQuery expression of their atoms, starting points for tunnels |
| `GET /version` | versions of the API (also of the pores tool) and MOLE |

The active sites come from [M-CSA](https://www.ebi.ac.uk/thornton-srv/m-csa/) (Ribeiro et al., Nucleic
Acids Research 2018, CC BY 4.0), converted by `tools/mcsa/convert.py` into `src/moleonline/api/resources/mcsa_sites.csv.gz`.
The example inputs (`/inputs/`) are in `src/moleonline/api/static/`; `/` redirects to the documentation, `/docs`.

A structure has at most 30 MB, uploaded (also when unpacked) or downloaded from PDBe: a single ribosome fits
(4V6X, 238,000 atoms, 23 MB), larger complexes do not. A larger upload is refused with 413, a larger PDBe entry
fails to load.

## Development

### Python

```sh
uv sync
export DATABASE_URL=postgresql+asyncpg://user:password@localhost/mole WORKING_DIRECTORY=/tmp/mole
uv run uvicorn moleonline.api.main:app
OPM_CACHE=/tmp/opm-cache MOLE=/path/to/mole2 MEMEMBED=/path/to/memembed uv run python -m moleonline.worker
```

The tests need a PostgreSQL database they may empty, e.g. a throwaway container (in memory, without fsync):

```sh
docker run -d --name moleonline-test-db -p 127.0.0.1:55432:5432 -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=test --tmpfs /var/lib/postgresql docker.io/library/postgres:18-alpine -c fsync=off
export TEST_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/test
uv run pytest    # the API with workers in threads, a fake MOLE and pores tool, and a fake PDBe
MOLE2=/path/to/mole2 MEMEMBED=/path/to/memembed uv run pytest    # also the tests needing them, skipped without
```

The code is checked with [ruff](https://docs.astral.sh/ruff/), configured in `pyproject.toml`:

```sh
uv run ruff check
```

### Database

The tables are described in `src/moleonline/core/storage.py`; the [Alembic](https://alembic.sqlalchemy.org/) revision in
`src/moleonline/core/migrations/versions` makes them. The API and the workers upgrade the database to it when they start,
in one transaction, one process at a time.

Until MOLEonline is deployed, there is one revision, made anew when the tables change (a test fails while it differs
from them), and databases are made anew too (`docker compose down -v`). The revision is made from an empty database:

```sh
docker exec moleonline-test-db psql -U test -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public'    # empty
rm src/moleonline/core/migrations/versions/*.py
DATABASE_URL=$TEST_DATABASE_URL uv run alembic revision --autogenerate --rev-id initial -m tables
```

### OpenAPI and the pores tool

`openapi.json` is the API's OpenAPI description, from which the web generates its types. After changing the API,
update it with `uv run python tools/openapi.py` (a test fails while it is out of date), then `npm run api-types` in
`web/`.

The pores tool (`src/moleonline/worker/pores`) runs as `python -m moleonline.worker.pores <pores.json>`, its input is described in
`cli.py`. Its tests (`tests/pores`) compare with production results of the original Pores, in `tests/pores/data/`
(see its README).

## Licence

MIT, see [`LICENSE`](../LICENSE). MEMEMBED in the worker's image is GPL v2.
