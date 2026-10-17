# MOLEonline API

Backend of [MOLEonline](https://github.com/sb-ncbr/moleonline-web): loads protein structures, computes
cavities, tunnels and pores with [MOLE 2](https://github.com/sb-ncbr/webchemistry) and serves the results.

The container contains:

- the API (Python, FastAPI, `src/mole_api`), with the .NET runtime for MOLE,
- MOLE 2, from the image `sbncbr/mole2`,
- the pores tool (`tools/pores`): the membrane position for pore computations, from
  [OPM](https://opm.phar.umich.edu/) or predicted by MEMEMBED,
- [MEMEMBED](https://github.com/psipred/MemEmbed), built from source (GPL v2, its source and licence
  are in `/opt/memembed` in the image).

## Running

```sh
docker network create moleonline    # once, shared with the web's compose project
docker compose up -d --build
```

The API has no port on the host. It listens on port 8080 in the Docker network `moleonline`, as the
service `api`, for the web's nginx container. Startup checks the configuration: if a path is wrong, the
container stops and its log says what is wrong.

```sh
docker compose logs -f api    # one line per event; requests are not logged, errors with the request path
```

To reach the API from the host, e.g. for debugging, publish the port in `compose.override.yaml`
(compose reads it next to `compose.yaml`):

```yaml
services:
  api:
    ports:
      - "127.0.0.1:8080:8080"
```

### nginx

The web's nginx container (`moleonline-web`, `docker/nginx.site.conf`) serves the API under `/api` and
proxies it to `api:8080` over the network `moleonline`. The network has to exist before either project
starts. The API takes the client address from `X-Forwarded-For` when the request comes from loopback or
a private network, as the Docker network is (the IP log, see below).

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
| `/data/computations/<id>/` | one computation: the structure, `_object.json` (status), one folder per submission |
| `/data/computations/Mole_api_IP_log.csv` | every load and submission request, also refused ones: time (UTC), `Init` or `Submit`, client address, `PDB`, `Upload`, `Mole` or `Pores`, computation id (`-` when none was created) |
| `/data/opm-cache/` | membrane files downloaded from OPM, reused for 30 days |

Usage per month or year (computations, PDB entries and uploads, submissions and their outcome, distinct client
addresses), from the status files and the IP log:

```sh
docker compose exec -T api /opt/pores/bin/python -I - /data/computations month < tools/stats.py
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
| `PORES` | `/opt/pores/bin/pores` | full path |

The pores tool reads `MEMEMBED` (the MEMEMBED executable) and `PORES_OPM_CACHE` (the OPM file cache)
from the environment; the image sets both. All times the API stores are in UTC.

## Endpoints

The OpenAPI documentation is at `/docs` (`/openapi.json`). Endpoint names match in any case and a trailing slash
is ignored, as in the former C# API (the web uses `/Compinfo/` and `/Init/`). All responses are JSON. A request that fails has an HTTP error status and a report with `Status: "Error"` and `ErrorMsg`:
400 invalid request (parameters, file type, data format), 404 no such computation, submission or data, 409 the computation
is busy or could not be loaded, 410 deleted, 413 upload too large, 503 all slots busy, 500 server error. A report about a
computation that failed (e.g. `Status` of a failed submission) is a successful request, 200.

| Endpoint | |
|---|---|
| `POST /Init/{pdbId}?assembly=` | load a PDB entry from PDBe and compute its cavities; `assembly`: `preferred` (default, PDBe's preferred biological assembly, the one OPM has; the asymmetric unit when PDBe cannot tell which one it is, e.g. when it is not reachable), `asymmetric` (the asymmetric unit) or an assembly id |
| `POST /Init` | load an uploaded structure (`multipart/form-data`, `.pdb`, `.cif`, `.pdb.gz`, `.cif.gz`); a gzipped file is unpacked and stored as `.pdb` or `.cif` |
| `POST /Submit/Mole/{id}` | compute tunnels, body: MOLE parameters |
| `POST /Submit/Pores/{id}` | compute a pore across the membrane, body: `InMembrane`, `IsBetaBarrel`, `Chains` (list of chain names, empty: all), `ProbeRadius`, `InteriorThreshold` (`null`: the default) |
| `GET /Status/{id}?submitId=` | status of a computation or one submission |
| `GET /CompInfo/{id}` | the computation and the parameters of all submissions |
| `GET /Data/{id}?submitId=&format=` | results: `json`, `report` (zip), `molecule`, `pymol`, `chimera`, `vmd`, `pdb`, `membrane` |
| `GET /CSA/{id}` | catalytic active sites of the PDB entry from M-CSA, one per copy, starting points for tunnels |
| `POST /Kill/{id}` | stop the running submission |
| `DELETE /Delete/{id}` | delete the computation |
| `GET /Version` | versions of the API, MOLE and Pores |

The active sites of `/CSA` come from [M-CSA](https://www.ebi.ac.uk/thornton-srv/m-csa/) (Ribeiro et al., Nucleic
Acids Research 2018, CC BY 4.0), converted by `tools/mcsa/convert.py` into `src/mole_api/resources/mcsa_sites.csv.gz`.
The documentation page and the example inputs (`/inputs/`) are in `src/mole_api/static/`.

A structure has at most 30 MB, uploaded (also when unpacked) or downloaded from PDBe: a single ribosome fits
(4V6X, 238,000 atoms, 23 MB), larger complexes do not. A larger upload is refused with 413, a larger PDBe entry
fails to load.

## Development

### Python API

```sh
uv sync
WORKING_DIRECTORY=/tmp/mole MOLE=/path/to/mole2 PORES=$PWD/tools/pores/.venv/bin/pores \
uv run uvicorn mole_api.main:app
uv run pytest    # with a fake MOLE and pores tool, and a fake PDBe
MOLE2=/path/to/mole2 uv run pytest    # also a tunnel computation with the real MOLE, skipped without
```

### Pores tool

The pores tool is a [uv](https://docs.astral.sh/uv/) project, its input is described in
`tools/pores/src/pores/cli.py`:

```sh
cd tools/pores
uv sync
MOLE2=/path/to/mole2 MEMEMBED=/path/to/memembed uv run pytest    # tests needing them are skipped without
```

The tests compare with production results of the original Pores, in `tools/pores/tests/data/` (see its README).

## Licence

MIT, see `LICENSE`. MEMEMBED in the container image is GPL v2.
