# MOLEonline in Kubernetes

The manifests for the [CERIT-SC](https://docs.cerit.io/) cluster `kuba-cluster`, managed with Kustomize (part of
`kubectl`):

- [`base/`](base): the whole application, without a namespace or a domain: PostgreSQL (CloudNativePG), a shared volume
  for structures and results, the API, the workers, the website and a Gateway with a Let's Encrypt certificate
- [`dev/`](dev): the dev instance, in the namespace `racek1-ns` at `moleonline-dev.biodata.ceitec.cz`

The images come from `ghcr.io/krab1k/moleonline-*`, published by CI from every commit on `main`. The dev instance
follows `latest`.

## Deploying

```sh
kubectl --context kuba-cluster apply -k deploy/k8s/dev
```

The domain needs a DNS CNAME to `kuba-lb.cloud.e-infra.cz`. The certificate is issued once the name resolves.

To pick up newly published images:

```sh
kubectl --context kuba-cluster -n racek1-ns rollout restart deployment
```

## Another environment

Copy `dev/` and change the namespace, the domain and, if needed, the number of workers (`replicas` of
`moleonline-worker`). For production, pin the images to a commit instead of `latest`:

```yaml
images:
  - name: ghcr.io/krab1k/moleonline-api
    newTag: <commit>
```

## Operating it

```sh
kubectl -n racek1-ns get pods
kubectl -n racek1-ns logs deploy/moleonline-api
kubectl -n racek1-ns logs deploy/moleonline-worker --all-containers --prefix
kubectl -n racek1-ns exec deploy/moleonline-api -- /app/.venv/bin/python -m moleonline.api.stats month
```

Restarting any deployment is safe: waiting computations stay queued, running ones fail as interrupted and can be
submitted again. The database of the dev instance has no backups.
