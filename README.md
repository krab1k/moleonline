# MOLEonline

MOLEonline is a web service for the location and characterisation of channels, tunnels and pores in
(bio)macromolecular structures, built on [MOLE 2](https://github.com/sb-ncbr/webchemistry).

| Directory | |
|---|---|
| [`backend/`](backend) | the MOLE API (Python, FastAPI): loads structures, runs MOLE and the pores tool, serves the results |
| [`web/`](web) | the web interface (TypeScript, React, Mol*) and its nginx container, which serves the API under `/api` |

Each directory has its own README with the details of running, configuring and developing it.

## Running

[`compose.yaml`](compose.yaml) runs them all: the service `web`, nginx with the site, which forwards `/api/...` to
the service `api`, which queues the computations; `worker`, which runs them, one at a time each; and `db`, PostgreSQL
with the sessions, jobs and their queue. Only the site has a port on the host.

```bash
git clone https://github.com/sb-ncbr/moleonline
cd moleonline
docker compose up -d --build
docker compose logs -f
```

Settings, in the environment or in `.env` next to `compose.yaml`:

| Setting | Default | |
|---|---|---|
| `MOLEONLINE_PORT` | `8080` | the site's port on the host |
| `WORKERS` | `4` | computations at once, a worker each; also `docker compose up -d --scale worker=N` |
| `WORKER_MEMORY_LIMIT` | `6g` | memory of one worker, its computation, see [`backend/README.md`](backend/README.md#configuration) |
| `POSTGRES_PASSWORD` | `mole` | the database's, set when its volume is made |

The API's data is in two volumes: `moleonline_db`, the database (sessions, jobs, the queue, the request log), and
`moleonline_data`, the sessions' files and OPM files. Until MOLEonline is deployed, the database's tables are made anew
when they change: `docker compose down -v` empties both (see [`backend/README.md`](backend/README.md#database)).

## History

Until October 2026, the API and the web were developed in separate repositories,
[moleonline-api](https://github.com/sb-ncbr/moleonline-api) and
[moleonline-web](https://github.com/sb-ncbr/moleonline-web). Their histories are merged here, with their tags
prefixed by `api/` and `web/`.

## Licence

MIT, see [`LICENSE`](LICENSE).
