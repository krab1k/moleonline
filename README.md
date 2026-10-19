# MOLEonline

MOLEonline is a web service for the location and characterisation of channels, tunnels and pores in
(bio)macromolecular structures, built on [MOLE 2](https://github.com/sb-ncbr/webchemistry).

| Directory | |
|---|---|
| [`api/`](api) | the MOLE API (Python, FastAPI): loads structures, runs MOLE and the pores tool, serves the results |
| [`web/`](web) | the web interface (TypeScript, React, Mol*) and its nginx container, which serves the API under `/api` |

Each directory has its own README with the details of running, configuring and developing it.

## Running

[`compose.yaml`](compose.yaml) runs both: the service `web`, nginx with the site, which forwards `/api/...` to
the service `api`. The API has no port on the host.

```bash
git clone https://github.com/sb-ncbr/moleonline
cd moleonline
docker compose up -d --build
docker compose logs -f
```

Settings, in the environment or in `.env` next to `compose.yaml` (locally; on the servers `.env` is the instance's, see below):

| Setting | Default | |
|---|---|---|
| `MOLEONLINE_PORT` | `8080` | the site's port on the host |
| `API_MEMORY_LIMIT` | `24g` | memory of all computations together, see [`api/README.md`](api/README.md#configuration) |

The API's data (computations, the submission log, OPM files) is in the volume `moleonline_data`.

### Production and testing

There are two instances, on separate machines, which differ only in their domains:

| Instance | Domains | Settings |
|---|---|---|
| production | `moleonline.biodata.ceitec.cz`, `moleonline.cz` | [`deploy/production.env`](deploy/production.env) |
| testing | `moleonline-dev.biodata.ceitec.cz` | [`deploy/dev.env`](deploy/dev.env) |

On each machine, `.env` in the repository's root links to the instance's settings. They add
[`compose.production.yaml`](compose.production.yaml), which serves the site on ports 80 and 443 with the
machine's Let's Encrypt certificate (using [certbot](https://certbot.eff.org/instructions?ws=other&os=snap)),
so plain `docker compose` commands run the instance. Assuming running Ubuntu 24.04 LTS:

```bash
# 0. Clone the repository into /home/ubuntu/moleonline and select the instance (dev.env on the testing machine)
git clone https://github.com/sb-ncbr/moleonline /home/ubuntu/moleonline
cd /home/ubuntu/moleonline
ln -s deploy/production.env .env

# 1. Install certbot and issue the certificate for the instance's domains (SERVER_NAMES in .env); certbot names
#    it after the first domain, which has to be CERT_NAME in .env
sudo snap install certbot
sudo certbot certonly --standalone -d moleonline.biodata.ceitec.cz -d moleonline.cz    # testing: -d moleonline-dev.biodata.ceitec.cz

# 2. Setup certificates renewal cron job
sudo cp web/docker/renew_certificates.sh /usr/local/bin
sudo chmod +x /usr/local/bin/renew_certificates.sh
sudo ln -s /usr/local/bin/renew_certificates.sh /etc/cron.weekly/renew_certificates.sh

# 3. Build and run the application
docker compose up -d --build
```

To deploy a new version: `git pull && docker compose up -d --build`, first on the testing machine.

## History

Until October 2026, the API and the web were developed in separate repositories,
[moleonline-api](https://github.com/sb-ncbr/moleonline-api) and
[moleonline-web](https://github.com/sb-ncbr/moleonline-web). Their histories are merged here, with their tags
prefixed by `api/` and `web/`.

## Licence

MIT, see [`LICENSE`](LICENSE).
