# MOLEonline

MOLEonline is a web service for the location and characterisation of channels, tunnels and pores in
(bio)macromolecular structures, built on [MOLE 2](https://github.com/sb-ncbr/webchemistry).

| Directory | |
|---|---|
| [`api/`](api) | the MOLE API (Python, FastAPI): loads structures, runs MOLE and the pores tool, serves the results |
| [`web/`](web) | the web interface (TypeScript, React, Mol*) and its nginx container, which serves the API under `/api` |

Each directory has its own README with the details of running, configuring and developing it.

## Running

The API and the web are two compose projects joined by the Docker network `moleonline`:

```bash
docker network create moleonline    # once
docker compose -f api/compose.yaml up -d --build
docker compose -f web/docker/docker-compose.yaml up -d --build
```

The site listens on port 8080 (`MOLEONLINE_PORT`). For production, see [`web/README.md`](web/README.md#production).

## History

Until October 2026, the API and the web were developed in separate repositories,
[moleonline-api](https://github.com/sb-ncbr/moleonline-api) and
[moleonline-web](https://github.com/sb-ncbr/moleonline-web). Their histories are merged here, with their tags
prefixed by `api/` and `web/`.

## Licence

MIT, see [`LICENSE`](LICENSE).
