# MOLEonline in Kubernetes

The manifests for the [CERIT-SC](https://docs.cerit.io/) cluster `kuba-cluster`, managed with Kustomize (part of
`kubectl`):

- [`base/`](base): the whole application, without a namespace or a domain: PostgreSQL (CloudNativePG), a shared volume
  for structures and results, the API, the workers, the website and a Gateway with a Let's Encrypt certificate
- [`dev/`](dev): the dev instance, in the namespace `racek1-ns` at `moleonline-dev.biodata.ceitec.cz`
- [`prod/`](prod): the production instance, in the namespace `moleonline-ns` at `moleonline.biodata.ceitec.cz` and
  `moleonline.cz`, with a nightly dump of the database

The images come from `ghcr.io/krab1k/moleonline-*`, published by CI from every commit on `main`. The dev instance
follows `latest`, production runs the images of the commit in `prod/kustomization.yaml`.

## Deploying

```sh
kubectl --context kuba-cluster apply -k deploy/k8s/dev
```

The domain needs a DNS CNAME to `kuba-lb.cloud.e-infra.cz`. The certificate is issued once the name resolves.

To pick up newly published images:

```sh
kubectl --context kuba-cluster -n racek1-ns rollout restart deployment
```

## Production

A release is a commit on `main` whose CI run passed: set it as `newTag` of all three images in
[`prod/kustomization.yaml`](prod/kustomization.yaml) and apply `prod/`.

Each domain has a certificate of its own, issued by HTTP-01: Let's Encrypt asks the domain itself, so a certificate is
issued only while its domain resolves to the cluster. `moleonline.biodata.ceitec.cz` is a CNAME to
`kuba-lb.cloud.e-infra.cz`; `moleonline.cz`, the zone's apex, cannot be a CNAME and has the A record of
`kuba-lb.cloud.e-infra.cz` (147.251.252.9). `mole.upol.cz`, the old domain, is a CNAME to `moleonline.cz`; it only
redirects there, keeping the path ([`prod/upol-redirect.yaml`](prod/upol-redirect.yaml)).

The database dumps (`pg_dump -Fc`, the last 14) are on the volume `moleonline-db-backup`, which CERIT-SC backs up for
30 days. To restore one into the running database:

```sh
kubectl -n moleonline-ns exec -i moleonline-db-1 -- pg_restore --clean --if-exists -d mole < moleonline-<date>.dump
```

## Another environment

Copy `dev/` and change the namespace, the domain and, if needed, the number of workers (`replicas` of
`moleonline-worker`).

## Operating it

```sh
kubectl -n racek1-ns get pods
kubectl -n racek1-ns logs deploy/moleonline-api
kubectl -n racek1-ns logs deploy/moleonline-worker --all-containers --prefix
kubectl -n racek1-ns exec deploy/moleonline-api -- /app/.venv/bin/python -m moleonline.api.stats month
```

Restarting any deployment is safe: waiting computations stay queued, running ones fail as interrupted and can be
submitted again. The database of the dev instance has no backups.
