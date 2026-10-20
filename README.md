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

The API's data (sessions, the request log, OPM files) is in the volume `moleonline_data`.

### Production and testing

There are two instances, on separate machines, which differ only in their domains:

| Instance | Domains | Settings |
|---|---|---|
| production | `moleonline.biodata.ceitec.cz`, `moleonline.cz` | [`deploy/production.env`](deploy/production.env) |
| testing | `moleonline-dev.biodata.ceitec.cz` | [`deploy/dev.env`](deploy/dev.env) |

On each machine, `.env` in the repository's root links to the instance's settings. They add
[`compose.production.yaml`](compose.production.yaml), which serves the site on ports 80 and 443, so plain
`docker compose` commands run the instance.

The certificate is Let's Encrypt's, from [certbot](https://certbot.eff.org/instructions?ws=other&os=snap), renewed
with `--webroot`: nginx serves the challenges from `/var/www/certbot`, so the site keeps running. After each
renewal, [`deploy/certbot-deploy-hook.sh`](deploy/certbot-deploy-hook.sh) copies the certificate to
`/var/lib/moleonline/certs`, where nginx (running as the user `nginx`) can read it, and reloads nginx. Assuming
running Ubuntu 24.04 LTS:

```bash
# 0. Clone the repository into /home/ubuntu/moleonline and select the instance (dev.env on the testing machine)
git clone https://github.com/sb-ncbr/moleonline /home/ubuntu/moleonline
cd /home/ubuntu/moleonline
ln -s deploy/production.env .env

# 1. Install certbot (it renews the certificates by its own timer) and the deploy hook
sudo snap install --classic certbot
sudo mkdir -p /var/www/certbot /etc/letsencrypt/renewal-hooks/deploy
sudo ln -s /home/ubuntu/moleonline/deploy/certbot-deploy-hook.sh /etc/letsencrypt/renewal-hooks/deploy/moleonline.sh

# 2. Issue the certificate for the instance's domains (SERVER_NAMES in .env). nginx needs it to start, so this once
#    certbot answers the challenge itself on port 80; the hook only runs on renewals, so run it now
DOMAIN=moleonline.biodata.ceitec.cz    # testing: moleonline-dev.biodata.ceitec.cz
sudo certbot certonly --standalone -d $DOMAIN -d moleonline.cz    # testing: only -d $DOMAIN
sudo RENEWED_LINEAGE=/etc/letsencrypt/live/$DOMAIN /etc/letsencrypt/renewal-hooks/deploy/moleonline.sh

# 3. Build and run the application
docker compose up -d --build

# 4. Renew through the running site from now on (certbot tests it with a dry run)
sudo certbot reconfigure --cert-name $DOMAIN --webroot -w /var/www/certbot
```

To deploy a new version: `git pull && docker compose up -d --build`, first on the testing machine.

## History

Until October 2026, the API and the web were developed in separate repositories,
[moleonline-api](https://github.com/sb-ncbr/moleonline-api) and
[moleonline-web](https://github.com/sb-ncbr/moleonline-web). Their histories are merged here, with their tags
prefixed by `api/` and `web/`.

## Licence

MIT, see [`LICENSE`](LICENSE).
