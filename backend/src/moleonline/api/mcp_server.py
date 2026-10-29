"""MOLEonline as an MCP server (https://modelcontextprotocol.io) at /mcp, for assistants: the tools call the API's
endpoints in the process, as the client that called the tool (its limits, its request log), and return summaries a
model can read; the full results stay in the API (a resource, links). Stateless and answered as JSON: any replica
answers, nothing streams through the proxies."""

import asyncio
import ipaddress
import json
import logging
import re
import urllib.parse
import zlib
from typing import Literal

import httpx2
from fastapi import FastAPI, Request
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.exceptions import HTTPException
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ..core.limits import MAX_STRUCTURE_MB, MB
from .upload import TOO_LARGE, stored_name

POLL_SECONDS = 2.0
MAX_WAIT_SECONDS = 45  # a tool call returns by then, the model asks again; below the proxies' timeouts (nginx: 60 s)
DOWNLOAD_SECONDS = 30  # of a structure at a URL, within the call's MAX_WAIT_SECONDS
MAX_CHANNELS = 20  # in an answer of get_job, the next ones from next_offset
MAX_REDIRECTS = 5
LIGAND = re.compile(r"[A-Z0-9]{1,5}")  # a PDB chemical component id; nothing else gets into MOLE's query
GIVEN_RESIDUE = re.compile(r"(\S+)\s+(-?\d+)([A-Za-z]?)")  # "A 308", chain and number; "A 59A" with an insertion code
QUERY_RESIDUES = re.compile(r"Residues\(([^()]*)\)")  # the residue names in a query: Residues('HEM'), Residues('HEM', 'HEC')
PROPERTIES = ("Charge", "Hydropathy", "Hydrophobicity", "Polarity", "Mutability")  # of a channel's, as ChannelsDB's
GROUPS = ("Paths", "Tunnels", "Pores", "MergedPores")  # of data.json, in ChannelsDB's order: a channel's ref is its position
# A residue as MOLE writes it (PdbResidue.ToString): name, author number, chain (none when the file has none), its
# insertion code (i:A), and Backbone when only the backbone lines the layer. The chain of a copy in an assembly has the
# copy's number after an underscore (PDBe's C_2, which MOLEonline computes on) or a dash (ChannelsDB's B-2).
RESIDUE = re.compile(r"(?P<name>\S+) (?P<number>-?\d+)(?: (?!i:)(?P<chain>\S+?)(?:[_-](?P<copy>\d+))?)?(?: i:(?P<insertion_code>\S))?")

# hints for clients, e.g. to run the reading tools without asking each time (also each poll); they trust them for nothing
READS = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
CREATES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
CREATES_FROM_URL = CREATES.model_copy(update={"open_world_hint": True})  # downloads from anywhere

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


def stated(value):
    """The parameters without their empty parts (no origin points, no query, ...), as the model reads them"""
    if isinstance(value, dict):
        return {k: v for k, v in ((k, stated(v)) for k, v in value.items()) if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [stated(v) for v in value]
    return value


def is_bottleneck(layer: dict) -> bool:
    # the flag is left out of the other layers
    return layer["LayerGeometry"].get("Bottleneck", False)


def geometry(channel: dict) -> dict:
    """Length, bottleneck and narrowest point of a channel. The bottleneck is the layer MOLE flags: MOLE does not count
    the first layer, which lies in the binding site the channel starts from, so the narrowest point may be narrower.
    Without exactly one flagged layer, the bottleneck is unavailable, with why (as ChannelsDB's); of several, the website
    shows the first one, which nothing says is the right one (in all local results MOLE flags exactly one)."""
    profile = channel["Profile"]
    narrowest = min(profile, key=lambda point: point["Radius"])
    summary = {"length": round(profile[-1]["Distance"], 1), "bottleneck_radius": None, "bottleneck_at": None,
               "min_radius": round(narrowest["Radius"], 2), "min_radius_at": round(narrowest["Distance"], 1)}
    flagged = [layer["LayerGeometry"] for layer in channel["Layers"]["LayersInfo"] if is_bottleneck(layer)]
    if len(flagged) == 1:
        summary |= {"bottleneck_radius": round(flagged[0]["MinRadius"], 2),
                    "bottleneck_at": [round(flagged[0]["StartDistance"], 1), round(flagged[0]["EndDistance"], 1)]}
    else:
        summary["unavailable"] = {"bottleneck_radius": "several_bottleneck_flags" if flagged else "no_bottleneck_flag"}
    return summary


def channels_of(data: dict) -> list[dict]:
    """The channels of MOLE's data.json, their refs counting from 1 in this order"""
    return [channel for group in GROUPS for channel in data["Channels"].get(group, [])]


def channel_summary(ref: int, channel: dict) -> dict:
    """A channel of MOLE's data.json: its type (Tunnel, Pore, Path: a pore job's pore is a Path), geometry and properties
    (averages over the lining residues)"""
    properties = {key.lower(): channel["Properties"].get(key) for key in PROPERTIES}
    return {"ref": ref, "type": channel["Type"], "id": channel["Id"]} | geometry(channel) | {"properties": properties}


def residue_parts(residue: str) -> dict:
    """The parts of a residue, so that a reader need not parse it: its chain is the deposited chain (author chain ID),
    also for a symmetry copy, whose number is in copy"""
    if (match := RESIDUE.fullmatch(residue)) is None:
        return dict.fromkeys(("name", "number", "insertion_code", "chain", "copy"))
    return {"name": match["name"], "number": int(match["number"]), "insertion_code": match["insertion_code"],
            "chain": match["chain"], "copy": int(match["copy"]) if match["copy"] else None}


def residues(layers: list[dict]) -> list[dict]:
    """The residues of layers in the order of their first appearance, each backbone_only when only its backbone lines
    them, with their parts"""
    found: dict[str, bool] = {}
    for layer in layers:
        for residue in layer["Residues"]:
            name = residue.removesuffix(" Backbone")
            found[name] = found.get(name, True) and residue != name
    return [{"residue": name} | residue_parts(name) | {"backbone_only": backbone} for name, backbone in found.items()]


def any_of(queries: list[str]) -> str:
    """One query of several, as MOLE's origin takes one"""
    return queries[0] if len(queries) == 1 else f"Or({','.join(queries)})"


def starting_point(origin: dict) -> list[dict]:
    """Where a job's tunnels start, from its Origin (also of the website's jobs), as ChannelsDB gives it, in the terms of
    compute_tunnels' start: residues as "chain number", a ligand by its names (its queries too, when they select more
    than the residues: a known cofactor's reactive atom), a point; a query naming no residues as it is; auto without any"""
    found = [{"start": "residues", "residues": [f"{r['Chain']} {r['SequenceNumber']}" for r in group]}
             for group in origin.get("Residues") or []]
    if query := origin.get("QueryExpression"):
        names = list(dict.fromkeys(name for listed in QUERY_RESIDUES.findall(query)
                                   for name in (n.strip(" '\"") for n in listed.split(",")) if name))
        if not names:
            found.append({"start": "query", "queries": [query]})
        else:
            plain = query == any_of([f"Residues('{name}')" for name in names])  # as compute_tunnels makes it of the names
            found.append({"start": "ligand", "ligands": names} | ({} if plain else {"queries": [query]}))
    found.extend({"start": "point", "point": [round(p["X"], 3), round(p["Y"], 3), round(p["Z"], 3)]} for p in origin.get("Points") or [])
    return found or [{"start": "auto"}]


def given_residue(residue: str) -> dict:
    """A residue of compute_tunnels as the API's origin takes it"""
    if (match := GIVEN_RESIDUE.fullmatch(residue.strip())) is None:
        raise MoleError(f'Residues must be "chain number", e.g. "A 308", not "{residue}".')
    chain, number, insertion_code = match.groups()
    if insertion_code:
        # the API's residues have no insertion code: the job would start from residue 59, not 59A
        raise MoleError(f'"{residue}" has an insertion code, which MOLEonline cannot start from yet. Use start=point with '
                        "the residue's coordinates, or the residues next to it.")
    return {"Chain": chain, "SequenceNumber": int(number)}


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
            host = urllib.parse.urlsplit(url).hostname
            try:
                async with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urllib.parse.urljoin(url, response.headers.get("Location", ""))
                        continue
                    if not response.is_success:
                        raise MoleError(f"The URL answered {response.status_code} {response.reason_phrase}.")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content += chunk
                        if len(content) > MAX_STRUCTURE_MB * MB:
                            raise MoleError(TOO_LARGE)
                    return bytes(content)
            # else only "Error executing tool": the host unreachable, its certificate not trusted, the connection broken
            except httpx2.TimeoutException:
                raise MoleError(f"The host [{host}] did not answer in time. Try again later or another source of the file.") from None
            except httpx2.HTTPError as e:
                raise MoleError(f"The download from [{host}] failed: {str(e) or type(e).__name__}. Check the URL, or use another "
                                "source of the file.") from None
    raise MoleError(f"The URL redirects more than {MAX_REDIRECTS} times.")


def structure_name(url: str, content: bytes) -> str:
    """The file's name for the upload, which takes PDB and mmCIF files by their extensions: the URL's, or of a URL without
    one (AlphaFill's https://alphafill.eu/v1/aff/P10635) mmCIF when a line of its start begins with data_ (after
    comments and blanks, which CIF allows before it; no PDB line does), else PDB; a file that is neither then fails to load, with
    the reason"""
    name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
    if stored_name(name):
        return name
    gzipped = content.startswith(b"\x1f\x8b")
    try:
        start = zlib.decompressobj(zlib.MAX_WBITS | 16).decompress(content[:MB], 64 * 1024) if gzipped else content[:64 * 1024]
    except zlib.error:
        start = b""  # not gzip after all: the upload says so
    cif = re.search(rb"^[ \t]*data_", start, re.MULTILINE)
    return (name or "structure") + (".cif" if cif else ".pdb") + (".gz" if gzipped else "")


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

    @server.tool(annotations=CREATES)
    async def load_structure(ctx: Context, pdb_id: str, assembly: str = "preferred", wait_seconds: int = 30) -> dict:
        """Loads a PDB entry into a new session, on which tunnels and pores are computed. assembly: preferred (PDBe's
        preferred assembly), asymmetric (the asymmetric unit) or an assembly id. The session is ready when loaded;
        wait_seconds (at most 45) waits for that."""
        s = await call(ctx, "POST", "/sessions", json={"pdb_id": pdb_id, "assembly": assembly})
        return session_view(ctx, await waited(ctx, f"/sessions/{s['id']}", wait_seconds, {"ready", "failed"}))

    @server.tool(annotations=CREATES_FROM_URL)
    async def load_structure_url(ctx: Context, url: str, wait_seconds: int = 30) -> dict:
        """Loads a structure file at an https URL (e.g. an AlphaFold or AlphaFill model) into a new session: PDB or
        mmCIF, also gzipped, at most 30 MB; by the URL's extension, or the content when it has none. The session is
        ready when loaded; wait_seconds (at most 45) waits for that. M-CSA knows no active sites of such a structure:
        compute tunnels from residues or automatic starting points."""
        started = asyncio.get_running_loop().time()
        try:
            async with asyncio.timeout(DOWNLOAD_SECONDS):  # all of it, httpx's timeout is of each read
                content = await downloaded(url)
        except TimeoutError:
            raise MoleError(f"The download took longer than {DOWNLOAD_SECONDS} s.") from None
        s = await call(ctx, "POST", "/sessions/upload", files={"file": (structure_name(url, content), content)})
        return session_view(ctx, await waited(ctx, f"/sessions/{s['id']}", wait_seconds, {"ready", "failed"}, started))

    @server.tool(annotations=READS)
    async def get_session(ctx: Context, session_id: str, wait_seconds: int = 0) -> dict:
        """The session's state (queued, loading, ready or failed), its structure and its jobs. Also of a session the user
        computed on the website: its address is .../online/<session_id>, or .../online/<session_id>/<job number>."""
        return session_view(ctx, await waited(ctx, f"/sessions/{session_id}", wait_seconds, {"ready", "failed"}))

    @server.tool(annotations=CREATES)
    async def compute_tunnels(ctx: Context, session_id: str,
                              start: Literal["active_site", "residues", "ligand", "point", "auto"] = "active_site",
                              residues: list[str] | None = None, ligands: list[str] | None = None,
                              point: list[float] | None = None, probe_radius: float = 5.0, bottleneck_radius: float = 1.25) -> dict:
        """Computes tunnels from a starting point to the surface. start: active_site (the active site from M-CSA, PDB
        entries only), residues (around the residues given as "A 308", chain and number, as the structure file numbers
        them; not with insertion codes yet), ligand (from each copy of the ligands or cofactors with these residue names,
        e.g. ["HEM"]; a known cofactor from its reactive atom), point (from the coordinates [x, y, z] in Å of the
        structure's frame, e.g. a pocket's centre from a predictor or a docked ligand) or auto (from the cavities MOLE
        finds). probe_radius (1.4-45 Å): larger finds wider mouths; bottleneck_radius (0-5 Å): the narrowest a tunnel
        may be, except over at most 3 Å of its length (MOLE's bottleneck tolerance). Returns the job, read it with
        get_job, and its starting_point as ChannelsDB gives it (of active_site, the residues of the first of the sites
        M-CSA knows, with active_sites_known). A ligand the structure does not have, or a point outside its cavities,
        gives a finished job without tunnels, not an error."""
        known = {}
        match start:
            case "active_site":
                sites = await call(ctx, "GET", f"/sessions/{session_id}/active-sites")
                if not sites:
                    raise MoleError("No active site is known for this structure (M-CSA). Use start=residues or start=auto.")
                origin = {"Residues": [[{"Chain": r["Chain"], "SequenceNumber": r["SequenceNumber"]} for r in sites[0]]]}
                known = {"active_sites_known": len(sites)}
            case "residues":
                if not residues:
                    raise MoleError('Give the residues as "chain number", e.g. ["A 308", "A 309"].')
                origin = {"Residues": [[given_residue(r) for r in residues]]}
            case "ligand":
                names = list(dict.fromkeys(name.strip().upper() for name in ligands or []))
                if not names or not all(LIGAND.fullmatch(name) for name in names):
                    raise MoleError('Give the ligands\' residue names (their PDB chemical component ids), e.g. ligands=["HEM"].')
                cofactors = await call(ctx, "GET", "/cofactors")
                origin = {"QueryExpression": any_of([cofactors.get(name, f"Residues('{name}')") for name in names])}
            case "point":
                if point is None or len(point) != 3:
                    raise MoleError("Give the point as its coordinates [x, y, z] in Å, e.g. point=[-19.3, -23.6, -14.2].")
                origin = {"Points": [dict(zip("XYZ", point, strict=True))]}
            case "auto":
                origin = {}
        parameters = {"Cavity": {"ProbeRadius": probe_radius}, "Tunnel": {"BottleneckRadius": bottleneck_radius}, "Origin": origin}
        job = await call(ctx, "POST", f"/sessions/{session_id}/jobs", json={"type": "tunnels", "parameters": parameters})
        return {"session_id": session_id, "number": job["number"], "state": job["state"], "starting_point": starting_point(origin)} | known

    @server.tool(annotations=CREATES)
    async def compute_pore(ctx: Context, session_id: str, in_membrane: bool = False, beta_barrel: bool = False,
                           chains: list[str] | None = None) -> dict:
        """Computes a pore across the membrane of a membrane protein: the membrane from OPM (the preferred assembly of a
        PDB entry) or predicted by MEMEMBED. in_membrane: only the part between the membrane's planes; chains: the pore's
        chains, all by default. Returns the job, read it with get_job."""
        parameters = {"InMembrane": in_membrane, "IsBetaBarrel": beta_barrel, "Chains": chains or []}
        job = await call(ctx, "POST", f"/sessions/{session_id}/jobs", json={"type": "pores", "parameters": parameters})
        return {"session_id": session_id, "number": job["number"], "state": job["state"]}

    @server.tool(annotations=READS)
    async def get_job(ctx: Context, session_id: str, number: int, wait_seconds: int = 30, offset: int = 0) -> dict:
        """The job's state (queued, running, finished, failed or cancelled) and what it computes with (the structure, a
        tunnel job's starting_point as compute_tunnels gives it, the parameters, MOLE's version); when finished, its
        channels, as MOLE lists them (not ranked), 20 at a time: a job with more has next_offset, to list the next ones
        with offset. Each channel: ref (for get_channel, with their lining residues); length (Å); bottleneck_radius, the
        narrowest radius of the layer MOLE flags as the bottleneck (not counting the start, in the binding site), with
        bottleneck_at, its distances from the start; min_radius, the narrowest point anywhere, with min_radius_at;
        properties (charge, hydropathy, hydrophobicity, polarity, mutability). wait_seconds (at most 45) waits for the
        end. Also of a job the user computed on the website: its address is .../online/<session_id>/<number>."""
        if offset < 0:
            raise MoleError("offset is the position to list the channels from (from next_offset).")
        job = await waited(ctx, f"/sessions/{session_id}/jobs/{number}", wait_seconds, {"finished", "failed", "cancelled"})
        s = await call(ctx, "GET", f"/sessions/{session_id}")
        web, api = links(ctx)
        starts = {"starting_point": starting_point(job["parameters"].get("Origin") or {})} if job["type"] == "tunnels" else {}
        structure = f"PDB entry {s['pdb_id']}, assembly {s['assembly_id'] or 'asymmetric unit'}" if s["pdb_id"] else "an uploaded structure"
        answer = {"source": "moleonline", "session_id": session_id, "number": number, "type": job["type"], "state": job["state"],
                  "error": job["error"] or None,
                  "computed_with": {"structure": structure} | starts | {"mole_version": job["mole_version"],
                                                                        "parameters": stated(job["parameters"])},
                  "web": f"{web}/online/{session_id}/{number}"}
        if job["state"] == "finished":
            data = await call(ctx, "GET", f"/sessions/{session_id}/jobs/{number}/results/data")
            channels = [channel_summary(ref, c) for ref, c in enumerate(channels_of(data), 1)]
            answer |= {"channels": channels[offset:offset + MAX_CHANNELS], "total_channels": len(channels)}
            if offset + MAX_CHANNELS < len(channels):
                answer["next_offset"] = offset + MAX_CHANNELS
            answer |= {"full_results": f"mole://sessions/{session_id}/jobs/{number}/data",
                       "downloads": {f: f"{api}/sessions/{session_id}/jobs/{number}/results/{f}" for f in ("report", "pymol", "pdb")}}
        return answer

    @server.tool(annotations=READS)
    async def get_channel(ctx: Context, session_id: str, number: int, channel: int) -> dict:
        """One channel of a finished job by its ref from get_job: its summary as there, its lining residues and its
        bottleneck residues (those of the flagged layer), in order along the channel. Each residue as MOLE writes it,
        for display ("THR 309 A", "ALA 5 C_2" in the second copy of chain C in an assembly, "i:A" an insertion code;
        ChannelsDB writes copies B-2), and its parts, to match residues by: name, number, insertion_code, chain (as
        deposited), copy. backbone_only: only its backbone atoms line the channel, in bottleneck_residues within the
        bottleneck layer."""
        job = await call(ctx, "GET", f"/sessions/{session_id}/jobs/{number}")
        if job["state"] != "finished":
            raise MoleError(f"The job is {job['state']}, it has no channels. Read it with get_job.")
        channels = channels_of(await call(ctx, "GET", f"/sessions/{session_id}/jobs/{number}/results/data"))
        if not 1 <= channel <= len(channels):
            raise MoleError(f"The job has no channel {channel}, its channels are 1 to {len(channels)} (ref in get_job)."
                            if channels else "The job found no channels.")
        found = channels[channel - 1]
        web, _ = links(ctx)
        summary = channel_summary(channel, found)
        layers = found["Layers"]["LayersInfo"]
        summary |= {"lining_residues": residues(layers), "bottleneck_residues": None}
        if summary["bottleneck_radius"] is None:
            # unknown, not none: for the same reason as the bottleneck itself
            summary["unavailable"]["bottleneck_residues"] = summary["unavailable"]["bottleneck_radius"]
        else:
            summary["bottleneck_residues"] = residues([layer for layer in layers if is_bottleneck(layer)])
        return {"source": "moleonline", "session_id": session_id, "number": number, "web": f"{web}/online/{session_id}/{number}",
                "channel": summary}

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
