# MOLEonline website

The website of MOLEonline: you load a structure, set up the computation and look at the channels it finds.
It is written in TypeScript with React, and uses [Mol*](https://molstar.org/) for the 3D view.

What it offers:

- tunnels and pores coloured by their physicochemical properties in the 3D view
- a 2D diagram of the structure and its tunnels, drawn by [2DProts](https://2dprots.ncbr.muni.cz/)
- known channels of PDB entries from [ChannelsDB](https://channelsdb2.biodata.ceitec.cz/)
- tunnels saved to and loaded from mmCIF files
- computation settings that can be saved and loaded again

## Deployment

The image built by [`docker/Dockerfile`](docker/Dockerfile) serves the site with nginx and forwards `/api/` to the
[backend](../backend). It runs together with the backend, see the [main README](../README.md#running-locally).

Its settings, as environment variables:

| Setting | Default | Meaning |
|---|---|---|
| `API_ADDRESS` | `api:8080` | Where nginx forwards `/api/` |
| `TRUSTED_PROXY` | none | A proxy in front of nginx (an address or a network): for its requests, the client is the address it added to `X-Forwarded-For` |
| `API_REQUESTS_PER_SECOND` | `50` | Requests to the API one client may make per second on average, with bursts of 200; more are refused with 429 |

nginx tells the API only the client's address, never what a client wrote into `X-Forwarded-For` itself; the API's own
limits rely on it. [`docker/test-clients.sh`](docker/test-clients.sh) checks both, CI runs it.

## Development

Start the whole application with Compose first (`docker compose up -d` in the repository's root). Then:

```sh
npm install
npm run dev
```

The development server forwards `/api` to the API of the Compose stack, `http://localhost:8080/api`. Change
`API_PROXY` in `.env.development` to use another one.

Other commands:

| Command | What it does |
|---|---|
| `npm test` | Unit tests (Vitest) |
| `npm run typecheck` | Type check |
| `npm run build` | Production build; needs `VITE_API_URL`, e.g. `VITE_API_URL=/api npm run build` |
| `npm run api-types` | Regenerates `sources/src/MoleAPISchema.d.ts` from `../backend/openapi.json` after the API changed |
