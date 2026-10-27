"""MOLEonline as an MCP server (https://modelcontextprotocol.io) at /mcp, for assistants: the tools call the API's
endpoints in the process, as the client that called the tool (its limits, its request log), and return summaries a
model can read; the full results stay in the API (a resource, links). Stateless and answered as JSON: any replica
answers, nothing streams through the proxies."""

import asyncio
import ipaddress
import json
import logging
import urllib.parse
from typing import Literal

import httpx2
from fastapi import FastAPI, Request
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.exceptions import HTTPException
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ..core.limits import MAX_STRUCTURE_MB, MB
from .upload import TOO_LARGE

POLL_SECONDS = 2.0
MAX_WAIT_SECONDS = 45  # a tool call returns by then, the model asks again; below the proxies' timeouts (nginx: 60 s)
DOWNLOAD_SECONDS = 30  # of a structure at a URL, within the call's MAX_WAIT_SECONDS
MAX_CHANNELS = 20  # in a summary, the rest only in the full results
MAX_REDIRECTS = 5

# not in the API's log: each tool's requests to the API (httpx2), each stateless request's end of its MCP session
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)


class MoleError(ToolError):
    """The API refused the request: its problem's detail, which the model reads (other exceptions only as an unexpected
    error)"""


def caller(ctx: Context) -> Request:
    return ctx.request_context.request


def links(ctx: Context) -> tuple[str, str]:
    """The web and the API as users reach them (the API under its proxy's prefix, /api)"""
    request = caller(ctx)
    web = (request.app.state.settings.public_url or f"{request.url.scheme}://{request.url.netloc}").rstrip("/")
    return web, web + request.scope.get("root_path", "")


def session_view(ctx: Context, s: dict) -> dict:
    web, _ = links(ctx)
    return {"session_id": s["id"], "state": s["state"], "error": s["error"] or None, "pdb_id": s["pdb_id"],
            "assembly": s["assembly_id"], "jobs": [{"number": j["number"], "type": j["type"], "state": j["state"]} for j in s["jobs"]],
            "web": f"{web}/online/{s['id']}"}


def channel_summary(kind: str, channel: dict) -> dict:
    """A channel of MOLE's data.json: its length, bottleneck and lining residues"""
    profile = channel["Profile"]
    residues = list(dict.fromkeys(r.removesuffix(" Backbone") for r in channel["Layers"]["ResidueFlow"]))
    properties = channel["Properties"]
    return {"type": kind, "id": channel["Id"], "length": round(profile[-1]["Distance"], 1),
            "bottleneck_radius": round(min(p["Radius"] for p in profile), 2),
            "charge": properties["Charge"], "hydropathy": properties["Hydropathy"], "polarity": properties["Polarity"],
            "lining_residues": residues}


async def check_public(url: str) -> None:
    """The server must not be made to reach addresses only it can (the cluster, the cloud's metadata); httpx resolves
    the name again, a resolver that changes its answer in between gets past this"""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise MoleError("Only https URLs are accepted.")
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, parsed.port or 443)
    except OSError:
        raise MoleError(f"The host [{parsed.hostname}] was not found.") from None
    if any(not ipaddress.ip_address(address[4][0].split("%")[0]).is_global for address in addresses):
        raise MoleError("Only public hosts are accepted.")


async def downloaded(url: str) -> bytes:
    """The file at a public https URL; redirects followed here, each target checked (httpx would not check them)"""
    async with httpx2.AsyncClient(timeout=60) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await check_public(url)
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = urllib.parse.urljoin(url, response.headers["Location"])
                    continue
                if not response.is_success:
                    raise MoleError(f"The URL answered {response.status_code} {response.reason_phrase}.")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content += chunk
                    if len(content) > MAX_STRUCTURE_MB * MB:
                        raise MoleError(TOO_LARGE)
                return bytes(content)
    raise MoleError(f"The URL redirects more than {MAX_REDIRECTS} times.")


def create_server(app: FastAPI) -> MCPServer:
    """The server of the app's endpoints; its route is added to the app (add_to), its session manager runs in the app's
    lifespan"""
    server = MCPServer(
        "MOLEonline",
        instructions="MOLEonline computes tunnels and pores in protein structures with MOLE 2. Load a structure (load_structure, "
                     "load_structure_url), then compute on its session (compute_tunnels, compute_pore) and read the job (get_job). "
                     "Computations take seconds to minutes: wait with wait_seconds, ask again while queued or running.",
    )

    async def call(ctx: Context, method: str, path: str, **kwargs) -> dict | list:
        """The API's answer to the caller's request, made in the process"""
        address = caller(ctx).client
        transport = httpx2.ASGITransport(app=app, client=(address.host, address.port) if address else ("unknown", 0))
        async with httpx2.AsyncClient(transport=transport, base_url="http://api") as client:
            response = await client.request(method, path, **kwargs)
        if response.is_error:
            try:
                detail = response.json()["detail"]  # RFC 9457
            except (ValueError, KeyError):
                detail = response.text or response.reason_phrase
            retry = f" Retry after {response.headers['Retry-After']} s." if "Retry-After" in response.headers else ""
            raise MoleError(f"{response.status_code}: {detail}{retry}")
        return response.json()

    async def waited(ctx: Context, path: str, wait_seconds: int, done: set[str], started: float | None = None) -> dict:
        """The session or job at path, polled until its state is in done or wait_seconds pass since started (the tool
        call's start, now by default): the whole call within MAX_WAIT_SECONDS"""
        start = asyncio.get_running_loop().time() if started is None else started
        deadline = start + min(max(wait_seconds, 0), MAX_WAIT_SECONDS)
        while True:
            found = await call(ctx, "GET", path)
            if found["state"] in done or asyncio.get_running_loop().time() + POLL_SECONDS > deadline:
                return found
            await asyncio.sleep(POLL_SECONDS)

    @server.tool()
    async def load_structure(ctx: Context, pdb_id: str, assembly: str = "preferred", wait_seconds: int = 30) -> dict:
        """Loads a PDB entry into a new session, on which tunnels and pores are computed. assembly: preferred (PDBe's
        preferred assembly), asymmetric (the asymmetric unit) or an assembly id. The session is ready when loaded;
        wait_seconds (at most 45) waits for that."""
        s = await call(ctx, "POST", "/sessions", json={"pdb_id": pdb_id, "assembly": assembly})
        return session_view(ctx, await waited(ctx, f"/sessions/{s['id']}", wait_seconds, {"ready", "failed"}))

    @server.tool()
    async def load_structure_url(ctx: Context, url: str, wait_seconds: int = 30) -> dict:
        """Loads a structure file at an https URL (e.g. an AlphaFold model) into a new session: PDB (*.pdb) or mmCIF
        (*.cif), also gzipped, at most 30 MB. The session is ready when loaded; wait_seconds (at most 45) waits for
        that. M-CSA knows no active sites of such a structure: compute tunnels from residues or automatic starting points."""
        started = asyncio.get_running_loop().time()
        try:
            async with asyncio.timeout(DOWNLOAD_SECONDS):  # all of it, httpx's timeout is of each read
                content = await downloaded(url)
        except TimeoutError:
            raise MoleError(f"The download took longer than {DOWNLOAD_SECONDS} s.") from None
        name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
        s = await call(ctx, "POST", "/sessions/upload", files={"file": (name, content)})
        return session_view(ctx, await waited(ctx, f"/sessions/{s['id']}", wait_seconds, {"ready", "failed"}, started))

    @server.tool()
    async def get_session(ctx: Context, session_id: str, wait_seconds: int = 0) -> dict:
        """The session's state (queued, loading, ready or failed), its structure and its jobs"""
        return session_view(ctx, await waited(ctx, f"/sessions/{session_id}", wait_seconds, {"ready", "failed"}))

    @server.tool()
    async def compute_tunnels(ctx: Context, session_id: str, start: Literal["active_site", "residues", "auto"] = "active_site",
                              residues: list[str] | None = None, probe_radius: float = 5.0, bottleneck_radius: float = 1.25) -> dict:
        """Computes tunnels from a starting point to the surface. start: active_site (the active site from M-CSA, PDB
        entries only), residues (around the residues given as "A 308", chain and number) or auto (from the cavities MOLE
        finds). probe_radius (1.4-45 Å): larger finds wider mouths; bottleneck_radius (0-5 Å): the narrowest a tunnel may
        be, except over at most 3 Å of its length (MOLE's bottleneck tolerance). Returns the job, read it with get_job."""
        match start:
            case "active_site":
                sites = await call(ctx, "GET", f"/sessions/{session_id}/active-sites")
                if not sites:
                    raise MoleError("No active site is known for this structure (M-CSA). Use start=residues or start=auto.")
                origin = {"Residues": [[{"Chain": r["Chain"], "SequenceNumber": r["SequenceNumber"]} for r in sites[0]]]}
            case "residues":
                if not residues:
                    raise MoleError('Give the residues as "chain number", e.g. ["A 308", "A 309"].')
                try:
                    origin = {"Residues": [[{"Chain": chain, "SequenceNumber": int(number)} for chain, number in map(str.split, residues)]]}
                except ValueError:
                    raise MoleError(f'Residues must be "chain number", e.g. "A 308", not {residues}.') from None
            case "auto":
                origin = {}
        parameters = {"Cavity": {"ProbeRadius": probe_radius}, "Tunnel": {"BottleneckRadius": bottleneck_radius}, "Origin": origin}
        job = await call(ctx, "POST", f"/sessions/{session_id}/jobs", json={"type": "tunnels", "parameters": parameters})
        return {"session_id": session_id, "number": job["number"], "state": job["state"]}

    @server.tool()
    async def compute_pore(ctx: Context, session_id: str, in_membrane: bool = False, beta_barrel: bool = False,
                           chains: list[str] | None = None) -> dict:
        """Computes a pore across the membrane of a membrane protein: the membrane from OPM (the preferred assembly of a
        PDB entry) or predicted by MEMEMBED. in_membrane: only the part between the membrane's planes; chains: the pore's
        chains, all by default. Returns the job, read it with get_job."""
        parameters = {"InMembrane": in_membrane, "IsBetaBarrel": beta_barrel, "Chains": chains or []}
        job = await call(ctx, "POST", f"/sessions/{session_id}/jobs", json={"type": "pores", "parameters": parameters})
        return {"session_id": session_id, "number": job["number"], "state": job["state"]}

    @server.tool()
    async def get_job(ctx: Context, session_id: str, number: int, wait_seconds: int = 30) -> dict:
        """The job's state (queued, running, finished, failed or cancelled); when finished, its channels: length and
        bottleneck radius (Å), charge, hydropathy, polarity and lining residues. wait_seconds (at most 45) waits for the end."""
        job = await waited(ctx, f"/sessions/{session_id}/jobs/{number}", wait_seconds, {"finished", "failed", "cancelled"})
        web, api = links(ctx)
        answer = {"session_id": session_id, "number": number, "type": job["type"], "state": job["state"], "error": job["error"] or None,
                  "web": f"{web}/online/{session_id}/{number}"}
        if job["state"] == "finished":
            data = await call(ctx, "GET", f"/sessions/{session_id}/jobs/{number}/results/data")
            channels = [channel_summary(kind, c) for kind, found in data["Channels"].items() for c in found]
            answer |= {"channels": channels[:MAX_CHANNELS], "total_channels": len(channels),
                       "full_results": f"mole://sessions/{session_id}/jobs/{number}/data",
                       "downloads": {f: f"{api}/sessions/{session_id}/jobs/{number}/results/{f}" for f in ("report", "pymol", "pdb")}}
        return answer

    @server.resource("mole://sessions/{session_id}/jobs/{number}/data", mime_type="application/json")
    async def job_data(ctx: Context, session_id: str, number: str) -> str:
        """MOLE's full results of a finished job (data.json): every channel's profile and layers"""
        return json.dumps(await call(ctx, "GET", f"/sessions/{session_id}/jobs/{number}/results/data"))

    return server


def add_to(app: FastAPI, server: MCPServer) -> None:
    """The server's route, /mcp. Stateless: no session is kept between requests, any replica answers; JSON answers, no
    event streams. Only POST: a GET would open the stream of the server's own messages, which a stateless server never
    sends, kept open with pings (a browser's visit, for ever); 405 says there is none, as MCP allows. No DNS rebinding
    protection, which guards servers on the user's machine: this one is public, without credentials to steal."""
    mcp_app = server.streamable_http_app(stateless_http=True, json_response=True,
                                         transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    [route] = mcp_app.routes
    app.router.routes.append(Route(route.path, PostOnly(route.endpoint)))


class PostOnly:
    """An ASGI app answering only POST, other methods 405 (the API's problem): the route itself matches them all, else
    the static files mounted at / would answer them"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["method"] != "POST":
            raise HTTPException(405, headers={"Allow": "POST"})
        await self.app(scope, receive, send)
