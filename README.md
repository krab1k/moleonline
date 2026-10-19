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

Settings, in the environment or in `.env` next to `compose.yaml`:

| Setting | Default | |
|---|---|---|
| `MOLEONLINE_PORT` | `8080` | the site's port on the host |
| `API_MEMORY_LIMIT` | `24g` | memory of all computations together, see [`api/README.md`](api/README.md#configuration) |

The API's data (computations, the submission log, OPM files) is in the volume `moleonline_data`.

### Production

For production, you need to have a certbot installed with Let's Encrypt certificates (using
[certbot](https://certbot.eff.org/instructions?ws=other&os=snap)) for both `moleonline.cz` and
`moleonline.biodata.ceitec.cz` domains. [`compose.production.yaml`](compose.production.yaml) serves the site on
ports 80 and 443 with them. Assuming running Ubuntu 24.04 LTS, use the following commands to run the application:

```bash
# 0. Clone the repository into /home/ubuntu/moleonline
git clone https://github.com/sb-ncbr/moleonline

# 1. Install certbot and issue the certificates
# - Follow certbot instructions
# - Use moleonline.biodata.ceitec.cz domain first, then moleonline.cz
sudo snap install certbot
sudo certbot certonly --standalone

# 2. Setup certificates renewal cron job
cp /home/ubuntu/moleonline/web/docker/renew_certificates.sh /usr/local/bin
chmod +x /usr/local/bin/renew_certificates.sh
ln -s /usr/local/bin/renew_certificates.sh /etc/cron.weekly/renew_certificates.sh

# 3. Build and run the application
# - Application assumes the certificates stored on the host in /etc/letsencrypt/live/moleonline.biodata.ceitec.cz directory
cd /home/ubuntu/moleonline
docker compose -f compose.yaml -f compose.production.yaml build
docker compose -f compose.yaml -f compose.production.yaml up -d
```

## History

Until October 2026, the API and the web were developed in separate repositories,
[moleonline-api](https://github.com/sb-ncbr/moleonline-api) and
[moleonline-web](https://github.com/sb-ncbr/moleonline-web). Their histories are merged here, with their tags
prefixed by `api/` and `web/`.

## Licence

MIT, see [`LICENSE`](LICENSE).
