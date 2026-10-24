# MOLEonline API

Backend of [MOLEonline](../web): loads protein structures, computes
cavities, tunnels and pores with [MOLE 2](https://github.com/sb-ncbr/webchemistry) and serves the results.

The container contains:

- the API (Python, FastAPI, `src/moleonline`: `api`, `worker` for the computations, `core` of both), with the .NET
  runtime for MOLE; it keeps the sessions, their jobs and the queue of their runs in PostgreSQL,
- the pores tool (`src/moleonline/worker/pores`, part of the API, run as a process of its own): the membrane position for
  pore computations, from [OPM](https://opm.phar.umich.edu/) or predicted by MEMEMBED,
- MOLE 2, from the image `sbncbr/mole2`,
- [MEMEMBED](https://github.com/psipred/MemEmbed), built from source (GPL v2, its source and licence
  are in `/opt/memembed` in the image).

## Running

The API deploys on its own, the web calls it from its own site (CORS). [`compose.yaml`](compose.yaml) runs it
on port `API_PORT` (default 8000), with its database (`db`, PostgreSQL, the password `POSTGRES_PASSWORD`). In this
directory:

```sh
docker compose up -d --build
docker compose logs -f    # one line per event; requests are not logged, errors with the request path
```

The settings (see [Configuration](#configuration)) go into `.env` next to `compose.yaml`, which compose also
reads for `API_PORT` and `API_MEMORY_LIMIT`. Startup checks them: if a path is wrong, the container stops and its
log says what is wrong.

### Behind a proxy

In production a reverse proxy (nginx, a Kubernetes ingress) serves the API over HTTPS. The API takes the client
address (the request log, see below) from `X-Forwarded-For` of the proxies in `FORWARDED_ALLOW_IPS` (uvicorn's), by
default any on loopback or a private network; with clients connecting to the API directly, set
`FORWARDED_ALLOW_IPS=127.0.0.1`. A proxy
serving the API under a path, e.g. `https://example.org/api/`, strips it and the API runs with
`UVICORN_ROOT_PATH=/api`, so that its documentation (`/api/docs`) and "Try it out" use the proxy's URLs. For nginx:

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 31m;    # uploaded structures, the API accepts files up to 30 MB, also when unpacked
}
```

The API runs as one process: the runs and their slots are kept in memory, so it runs as one container (one replica),
with its data on a persistent volume.

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

The API runs the queued runs in its slots, one API process for now. Queued runs wait over a restart; runs running
during a restart are reported as failed ("interrupted by a restart"), the user can submit them again. When the
database fails while a run runs, the API stops, to be restarted (compose, Kubernetes) and report it so; a run records
the MOLE that ran it.

## Configuration

Settings are environment variables, the defaults are the container's (`src/moleonline/core/config.py`):

| Setting | Default | |
|---|---|---|
| `WORKING_DIRECTORY` | `/data/computations` | the sessions' files, must be writable |
| `DATABASE_URL` | none, required | `postgresql+asyncpg://user:password@host/database`; the API waits for it when it starts, then brings its tables to its code's (see [Database](#database)) |
| `MAX_CONCURRENT_COMPUTATIONS` | `4` | MOLE and pore runs at once (loads included), others wait in a queue |
| `MAX_QUEUED_COMPUTATIONS` | `20` | runs waiting for a slot; a new session or job beyond them is refused (503 with `Retry-After`), `0`: none waits |
| `MAX_COMPUTATION_MINUTES` | `30` | a run taking longer is stopped and reported as an error (a ribosome takes about 2 minutes) |
| `MOLE` | `/opt/mole2/mole2` | full path |
| `MEMEMBED` | `/opt/memembed/memembed` | full path, the membrane of pore computations without OPM's |
| `OPM_CACHE` | `/data/opm-cache` | membrane files downloaded from OPM, must be writable |
| `CORS_ORIGINS` | `["*"]` | sites whose pages may call the API, a JSON list, e.g. `["https://moleonline.cz"]` |
| `FORWARDED_ALLOW_IPS` | private networks | reverse proxies whose `X-Forwarded-For` gives the client's address (the request log), uvicorn's setting: addresses and networks separated by commas; `127.0.0.1` when clients connect directly, so that none can choose its address |
| `UVICORN_ROOT_PATH` | none | the path prefix of a proxy serving the API under it, e.g. `/api` (uvicorn's `--root-path`) |

All times the API stores are in UTC.

The container's memory, all computations together, is limited to `API_MEMORY_LIMIT` (compose, default `24g`, in
`.env` next to `compose.yaml`). Above it the kernel stops the largest process, a computation, which is reported as
having run out of memory; the API keeps running.

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

### Python API

```sh
uv sync
DATABASE_URL=postgresql+asyncpg://user:password@localhost/mole WORKING_DIRECTORY=/tmp/mole OPM_CACHE=/tmp/opm-cache \
MOLE=/path/to/mole2 MEMEMBED=/path/to/memembed uv run uvicorn moleonline.api.main:app
```

The tests need a PostgreSQL database they may empty, e.g. a throwaway container (in memory, without fsync):

```sh
docker run -d --name moleonline-test-db -p 127.0.0.1:55432:5432 -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=test --tmpfs /var/lib/postgresql/data docker.io/library/postgres:17-alpine -c fsync=off
export TEST_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/test
uv run pytest    # with a fake MOLE and pores tool in the API's tests, and a fake PDBe
MOLE2=/path/to/mole2 MEMEMBED=/path/to/memembed uv run pytest    # also the tests needing them, skipped without
```

The code is checked with [ruff](https://docs.astral.sh/ruff/), configured in `pyproject.toml`:

```sh
uv run ruff check
```

### Database

The tables are described in `src/moleonline/core/storage.py`; the [Alembic](https://alembic.sqlalchemy.org/) revision in
`src/moleonline/core/migrations/versions` makes them. The API upgrades the database to it when it starts, in one transaction.

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

MIT, see [`LICENSE`](../LICENSE). MEMEMBED in the container image is GPL v2.
