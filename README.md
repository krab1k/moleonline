# MOLEonline

[![MOLEonline paper](https://img.shields.io/badge/Bioinformatics-2025-blue)](https://doi.org/10.1093/bioinformatics/btaf486)
[![MOLE 2.0 paper](https://img.shields.io/badge/J%20Cheminform-2013-blue)](https://doi.org/10.1186/1758-2946-5-39)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![Tests](https://github.com/sb-ncbr/moleonline/actions/workflows/test-build.yml/badge.svg?branch=main)](https://github.com/sb-ncbr/moleonline/actions/workflows/test-build.yml)

MOLEonline finds and describes channels, tunnels and pores in proteins and other macromolecular structures. It is a
web interface to [MOLE 2](https://github.com/sb-ncbr/webchemistry): load a PDB entry or upload your own structure,
compute tunnels or a pore through the membrane, and explore them in a 3D view and a 2D diagram.

The public instance runs at [moleonline.biodata.ceitec.cz](https://moleonline.biodata.ceitec.cz). AI assistants can
use it through its [MCP server](backend/README.md#mcp-server).

## Repository

- [`backend/`](backend): the API and the workers that run the computations (Python)
- [`web/`](web): the website (TypeScript, React, Mol*)

Each directory has its own README with more detail.

## Running locally

You need Docker or Podman with Compose. Then:

```bash
git clone https://github.com/sb-ncbr/moleonline
cd moleonline
docker compose up -d --build
```

The site is at http://localhost:8080. Compose starts the website, the API, four workers and a PostgreSQL database.
`docker compose logs -f` shows what they are doing.

You can change these settings in the environment or in a `.env` file next to `compose.yaml`:

| Setting | Default | Meaning |
|---|---|---|
| `MOLEONLINE_PORT` | `8080` | Port of the site on your machine |
| `WORKERS` | `4` | How many computations run at once |
| `WORKER_MEMORY_LIMIT` | `6g` | Memory for each computation |
| `POSTGRES_PASSWORD` | `mole` | Database password, used when the database is first created |

Data is kept in two volumes, `moleonline_db` (the database) and `moleonline_data` (structures and results). To start
again with no data, run `docker compose down -v`.

Compose is meant for local use and development. Deployments run in Kubernetes, see [`deploy/k8s`](deploy/k8s).

## Citing

If MOLEonline helps your research, please cite:

> Raček T., Vel'ký D., Bučeková G., Schindler O., Hutařová Vařeková I., Špačková A., Bazgier V., Berka K.,
> Svobodová R. MOLEonline: a web-based tool for analysing channels, tunnels, and pores (2025 update).
> *Bioinformatics* 41(9), btaf486 (2025). https://doi.org/10.1093/bioinformatics/btaf486

If you use these parts of it, please cite them too:

- The computations, by MOLE 2.0: Sehnal D. et al. MOLE 2.0: advanced approach for analysis of biomacromolecular
  channels. *Journal of Cheminformatics* 5, 39 (2013). https://doi.org/10.1186/1758-2946-5-39
- Known channels from ChannelsDB: Špačková A. et al. ChannelsDB 2.0: a comprehensive database of protein tunnels and
  pores in AlphaFold era. *Nucleic Acids Research* 52(D1), D413–D418 (2024). https://doi.org/10.1093/nar/gkad1012
- The 2D diagrams from 2DProts: Hutařová Vařeková I. et al. 2DProts: database of family-wide protein secondary
  structure diagrams. *Bioinformatics* 37(23), 4599–4601 (2021). https://doi.org/10.1093/bioinformatics/btab505

A step-by-step guide to MOLEonline and ChannelsDB is in Špačková A. et al., Analysis and Visualization of Protein
Channels, Tunnels, and Pores with MOLEonline and ChannelsDB 2.0, *Methods in Molecular Biology*, 219–233 (2024).
https://doi.org/10.1007/978-1-0716-4007-4_12

## History

Until October 2026 the API and the website lived in separate repositories,
[moleonline-api](https://github.com/sb-ncbr/moleonline-api) and
[moleonline-web](https://github.com/sb-ncbr/moleonline-web). Both histories were merged into this one, and their tags
got the prefixes `api/` and `web/`.

## Licence

MIT, see [`LICENSE`](LICENSE).
