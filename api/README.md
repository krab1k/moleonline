# MOLEonline API

Backend of [MOLEonline](https://github.com/sb-ncbr/moleonline-web): loads protein structures, computes
cavities, tunnels and pores with [MOLE 2](https://github.com/sb-ncbr/webchemistry) and serves the results.

The container contains:

- the API (ASP.NET Core, .NET 10),
- MOLE 2, from the image `sbncbr/mole2`,
- the pores tool (`tools/pores`): the membrane position for pore computations, from
  [OPM](https://opm.phar.umich.edu/) or predicted by MEMEMBED,
- [MEMEMBED](https://github.com/psipred/MemEmbed), built from source (GPL v2, its source and licence
  are in `/opt/memembed` in the image).

## Running

```sh
docker compose up -d --build
```

The API listens on `127.0.0.1:8080`, only for a reverse proxy on the same machine; another port is set
with `API_PORT=8081 docker compose up -d`. Startup checks the configuration: if a path is wrong, the
container stops and its log says what is wrong.

```sh
docker compose logs -f api    # one line per event; requests are not logged, errors with the request path
```

### nginx

The API takes the client address from `X-Forwarded-For` when the request comes from loopback or a
private network (the IP log, see below).

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8080/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 31m;    # uploaded structures, the API accepts files up to 30 MB
}
```

### Data

Everything is in the volume `data`, mounted at `/data`:

| Path | Content |
|---|---|
| `/data/computations/<id>/` | one computation: the structure, `_object.json` (status), one folder per submission |
| `/data/computations/Mole_api_IP_log.csv` | time and client address of every submission |
| `/data/opm-cache/` | membrane files downloaded from OPM, reused for 30 days |

Computations running during a restart are reported as failed ("interrupted by a restart"); the user
can submit them again.

## Configuration

Settings are in `appsettings.json` (container paths) and can be overridden with environment variables,
`Config__<name>`:

| Setting | Default | |
|---|---|---|
| `Config__WorkingDirectory` | `/data/computations` | computations, must be writable |
| `Config__MaxConcurentComputations` | `4` | MOLE and pore runs at once (loads included), others wait in a queue |
| `Config__MaxComputationMinutes` | `30` | a run taking longer is stopped and reported as an error (a ribosome takes about 2 minutes) |
| `Config__MOLE` | `/opt/mole2/mole2` | full path |
| `Config__Pores` | `/opt/pores/bin/pores` | full path |
| `Config__CSA` | `Resources/CSA_2_0_121113.txt` | active sites from the Catalytic Site Atlas, relative to the app |

The pores tool reads `MEMEMBED` (the MEMEMBED executable) and `PORES_OPM_CACHE` (the OPM file cache)
from the environment; the image sets both. `TZ` (in `compose.yaml`) sets the time of the IP log.

## Endpoints

All responses are JSON. A request that fails has an HTTP error status and a report with `Status: "Error"` and `ErrorMsg`:
400 invalid request (parameters, file type, data format), 404 no such computation, submission or data, 409 the computation
is busy or could not be loaded, 410 deleted, 413 upload too large, 503 all slots busy, 500 server error. A report about a
computation that failed (e.g. `Status` of a failed submission) is a successful request, 200.

| Endpoint | |
|---|---|
| `POST /Init/{pdbId}?assemblyId=` | load a PDB entry from PDBe and compute its cavities |
| `POST /Init/Pores/{pdbId}` | load the preferred biological assembly of a PDB entry, for a pore computation |
| `POST /Init` | load an uploaded structure (`multipart/form-data`, `.pdb`, `.cif`, `.pdb.gz`, `.cif.gz`) |
| `POST /Submit/Mole/{id}` | compute tunnels, body: MOLE parameters |
| `POST /Submit/Pores/{id}` | compute a pore across the membrane, body: `InMembrane`, `IsBetaBarel`, `Chains`, `ProbeRadius`, `InteriorThreshold` |
| `GET /Status/{id}?submitId=` | status of a computation or one submission |
| `GET /CompInfo/{id}` | the computation and the parameters of all submissions |
| `GET /Data/{id}?submitId=&format=` | results: `json`, `report` (zip), `molecule`, `pymol`, `chimera`, `vmd`, `pdb`, `membrane` |
| `GET /CSA/{id}` | catalytic active sites of the PDB entry, starting points for tunnels |
| `POST /Kill/{id}` | stop the running submission |
| `DELETE /Delete/{id}` | delete the computation |
| `GET /Version` | versions of the API, MOLE and Pores |

## Development

Run the API outside the container with the paths of local tools (`ASPNETCORE_ENVIRONMENT=Development`
also logs requests):

```sh
Config__WorkingDirectory=/tmp/mole Config__MOLE=/path/to/mole2 \
Config__Pores=$PWD/tools/pores/.venv/bin/pores MEMEMBED=/path/to/memembed \
dotnet run
```

The pores tool is a [uv](https://docs.astral.sh/uv/) project, its input is described in
`tools/pores/src/pores/cli.py`:

```sh
cd tools/pores
uv sync
MOLE2=/path/to/mole2 MEMEMBED=/path/to/memembed uv run pytest    # tests needing them are skipped without
```

The tests compare with production results in `tools/pores/reference/`, which is not in the repository yet.

## Licence

MIT, see `LICENSE`. MEMEMBED in the container image is GPL v2.
