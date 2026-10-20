# MOLEonline API

Backend of [MOLEonline](../web): loads protein structures, computes
cavities, tunnels and pores with [MOLE 2](https://github.com/sb-ncbr/webchemistry) and serves the results.

The container contains:

- the API (Python, FastAPI, `src/mole_api`), with the .NET runtime for MOLE,
- the pores tool (`src/mole_api/pores`, part of the API, run as a process of its own): the membrane position for
  pore computations, from [OPM](https://opm.phar.umich.edu/) or predicted by MEMEMBED,
- MOLE 2, from the image `sbncbr/mole2`,
- [MEMEMBED](https://github.com/psipred/MemEmbed), built from source (GPL v2, its source and licence
  are in `/opt/memembed` in the image).

## Running

The API runs with the web from the repository's [`compose.yaml`](../compose.yaml), see the
[README](../README.md#running). From the repository's root:

```sh
docker compose up -d --build
```

The API has no port on the host. It listens on port 8080 in the compose project's network, as the
service `api`, for the web's nginx container. Startup checks the configuration: if a path is wrong, the
container stops and its log says what is wrong.

```sh
docker compose logs -f api    # one line per event; requests are not logged, errors with the request path
```

To reach the API from the host, e.g. for debugging, publish the port in `compose.override.yaml`
(compose reads it next to `compose.yaml`, in the repository's root):

```yaml
services:
  api:
    ports:
      - "127.0.0.1:8080:8080"
```

### nginx

The web's nginx container (the service `web`, `web/docker/site.conf` and `web/docker/conf.d/api.conf.template`)
serves the API under `/api` and proxies it to `api:8080` over the compose project's network. The API takes the
client address from `X-Forwarded-For` when the request comes from loopback or a private network, as the Docker
network is (the IP log, see below). The API runs with `--root-path /api`, so its
documentation (`/api/docs`) and "Try it out" use the proxy's URLs; on a port published for debugging the endpoints
work, the documentation does not.

```nginx
location /api/ {
    proxy_pass http://api:8080/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 31m;    # uploaded structures, the API accepts files up to 30 MB, also when unpacked
}
```

### Data

Everything is in the volume `data`, mounted at `/data`:

| Path | Content |
|---|---|
| `/data/computations/<id>/` | one session: `session.json` (the session and its jobs, as the API answers them), the structure, `load/` (the load's files), `jobs/<n>/` (a job's files) |
| `/data/computations/Mole_api_IP_log.csv` | every request creating a session or a job, also refused ones: time (UTC), `Init` (a session) or `Submit` (a job), client address, `PDB`, `Upload`, `Mole` (tunnels) or `Pores` (`-`: a job request without a type), session id (`-` when none was created) |
| `/data/opm-cache/` | membrane files downloaded from OPM, reused for 30 days |

Usage per month or year (sessions of PDB entries and uploads, jobs and their outcome, distinct client
addresses), from the status files and the IP log:

```sh
docker compose exec -T api /app/.venv/bin/python -I - /data/computations month < api/tools/stats.py
```

Computations running during a restart are reported as failed ("interrupted by a restart"); the user
can submit them again.

## Configuration

Settings are environment variables, the defaults are the container's (`src/mole_api/config.py`):

| Setting | Default | |
|---|---|---|
| `WORKING_DIRECTORY` | `/data/computations` | computations, must be writable |
| `MAX_CONCURRENT_COMPUTATIONS` | `4` | MOLE and pore runs at once (loads included), others wait in a queue |
| `MAX_COMPUTATION_MINUTES` | `30` | a run taking longer is stopped and reported as an error (a ribosome takes about 2 minutes) |
| `MOLE` | `/opt/mole2/mole2` | full path |
| `MEMEMBED` | `/opt/memembed/memembed` | full path, the membrane of pore computations without OPM's |
| `OPM_CACHE` | `/data/opm-cache` | membrane files downloaded from OPM, must be writable |

All times the API stores are in UTC.

The container's memory, all computations together, is limited to `API_MEMORY_LIMIT` (compose, default `24g`, e.g. in
`.env` next to the repository's `compose.yaml`). Above it the kernel stops the largest process, a computation, which is reported as
having run out of memory; the API keeps running.

## Endpoints

The OpenAPI documentation is at `/docs` (`/openapi.json`). Responses are JSON, except the files to download. A
request that fails has an HTTP error status and problem details ([RFC 9457](https://www.rfc-editor.org/rfc/rfc9457),
`application/problem+json`): `type` (`about:blank`), `title` (the status' phrase), `status` and `detail` (why), with
`errors` (`field`, `message`) for a request that failed validation. 404 no such session, job or data, 409 a job runs on
the session or its structure could not be loaded, 410 deleted, 413 upload too large, 422 invalid request (body, parameters,
PDB id, file type, data format), 503 all slots busy, 500 server error. A session or job that failed is a successful
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
Acids Research 2018, CC BY 4.0), converted by `tools/mcsa/convert.py` into `src/mole_api/resources/mcsa_sites.csv.gz`.
The example inputs (`/inputs/`) are in `src/mole_api/static/`; `/` redirects to the documentation, `/docs`.

A structure has at most 30 MB, uploaded (also when unpacked) or downloaded from PDBe: a single ribosome fits
(4V6X, 238,000 atoms, 23 MB), larger complexes do not. A larger upload is refused with 413, a larger PDBe entry
fails to load.

## Development

### Python API

```sh
uv sync
WORKING_DIRECTORY=/tmp/mole OPM_CACHE=/tmp/opm-cache MOLE=/path/to/mole2 MEMEMBED=/path/to/memembed \
uv run uvicorn mole_api.main:app
uv run pytest    # with a fake MOLE and pores tool in the API's tests, and a fake PDBe
MOLE2=/path/to/mole2 MEMEMBED=/path/to/memembed uv run pytest    # also the tests needing them, skipped without
```

`openapi.json` is the API's OpenAPI description, from which the web generates its types. After changing the API,
update it with `uv run python tools/openapi.py` (a test fails while it is out of date), then `npm run api-types` in
`web/`.

The pores tool (`src/mole_api/pores`) runs as `python -m mole_api.pores <pores.json>`, its input is described in
`cli.py`. Its tests (`tests/pores`) compare with production results of the original Pores, in `tests/pores/data/`
(see its README).

## Licence

MIT, see [`LICENSE`](../LICENSE). MEMEMBED in the container image is GPL v2.
