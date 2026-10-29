"""/mcp: the MCP server, its tools calling the API in the process"""

import asyncio
import json
import time

import pytest

from moleonline.api import mcp_server

from .conftest import MOLE_VERSION, STRUCTURE, query, upload, wait, wait_session

TUNNEL = {"Id": "1", "Properties": {"Charge": 1, "Hydropathy": -0.5, "Polarity": 10.0},
          "Profile": [{"Radius": 1.5, "Distance": 0.0}, {"Radius": 1.1, "Distance": 4.0}, {"Radius": 2.0, "Distance": 12.34}],
          "Layers": {"ResidueFlow": ["THR 309 A", "GLY 310 A Backbone", "THR 309 A", "GLY 310 A"]}}


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch):
    monkeypatch.setattr(mcp_server, "POLL_SECONDS", 0.05)


def rpc(client, method: str, params: dict | None = None) -> dict:
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                           headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def tool(client, name: str, **arguments) -> tuple[bool, dict | str]:
    """Whether the tool failed, and its answer (the error's text when it did)"""
    result = rpc(client, "tools/call", {"name": name, "arguments": arguments})
    text = result["content"][0]["text"]
    return result.get("isError", False), text if result.get("isError") else json.loads(text)


def test_tools(client):
    """With hints for clients: reading ones read only, the others create, the URL one reaches outside"""
    hints = {t["name"]: t["annotations"] for t in rpc(client, "tools/list")["tools"]}
    assert set(hints) == {"load_structure", "load_structure_url", "get_session", "compute_tunnels", "compute_pore", "get_job"}
    assert {name for name, h in hints.items() if h["readOnlyHint"]} == {"get_session", "get_job"}
    assert not any(h.get("destructiveHint") for h in hints.values())
    assert {name for name, h in hints.items() if h["openWorldHint"]} == {"load_structure_url"}


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_only_post(client, method):
    """No stream of the server's messages (GET), no session to end (DELETE)"""
    response = client.request(method, "/mcp", headers={"Accept": "text/event-stream"})
    assert (response.status_code, response.headers["Allow"], response.json()["status"]) == (405, "POST", 405)


def test_the_apis_problems_reach_the_model(client):
    assert tool(client, "load_structure", pdb_id="nope") == (True, "Error executing tool load_structure: 422: Invalid PDB id [nope].")
    failed, text = tool(client, "get_session", session_id="missing")
    assert failed and "404: Session [missing] does not exist." in text


def test_tunnels_and_their_summary(api, tmp_path):
    (tmp_path / "data.json").write_text(json.dumps({"Channels": {"Tunnels": [TUNNEL]}}))
    client = api(run=f'mkdir -p "$(dirname "$1")/json" && cp {tmp_path / "data.json"} "$(dirname "$1")/json/data.json"')
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    wait_session(client, id)

    assert tool(client, "compute_tunnels", session_id=id) == (
        True, "Error executing tool compute_tunnels: No active site is known for this structure (M-CSA). Use start=residues or start=auto.")
    assert tool(client, "compute_tunnels", session_id=id, start="residues", residues=["A 308", "A 309"]) == (
        False, {"session_id": id, "number": 1, "state": "queued", "starting_point": {"residues": ["A 308", "A 309"]}})
    failed, job = tool(client, "get_job", session_id=id, number=1, wait_seconds=10)
    assert not failed and job["state"] == "finished"
    computed_with = job["computed_with"]  # what the answer is of: the structure, MOLE, the parameters without empty parts
    assert (computed_with["structure"], computed_with["mole_version"]) == ("an uploaded structure", MOLE_VERSION)
    parameters = computed_with["parameters"]
    assert parameters["Origin"] == {"Residues": [[{"Chain": "A", "SequenceNumber": 308}, {"Chain": "A", "SequenceNumber": 309}]]}
    assert (parameters["Cavity"]["ProbeRadius"], parameters["Tunnel"]["BottleneckTolerance"]) == (5.0, 3.0)
    assert "CustomExits" not in parameters and "QueryFilter" not in parameters
    assert job["channels"] == [{"type": "Tunnels", "id": "1", "length": 12.3, "bottleneck_radius": 1.1, "charge": 1, "hydropathy": -0.5,
                                "polarity": 10.0, "lining_residues": ["THR 309 A", "GLY 310 A"]}]
    assert job["web"] == f"http://testserver/online/{id}/1"  # from the request without PUBLIC_URL
    assert job["downloads"]["pymol"] == f"http://testserver/sessions/{id}/jobs/1/results/pymol"
    client.app.state.settings.public_url = "https://moleonline.example.org/"  # what users see, not the proxy's request
    assert tool(client, "get_job", session_id=id, number=1)[1]["web"] == f"https://moleonline.example.org/online/{id}/1"

    assert query(f"SELECT programmatic FROM runs WHERE session_id = '{id}' AND number = 1") == [(True,)]  # an assistant's
    [submitted] = client.get(f"/sessions/{id}").json()["jobs"]  # a job like any other
    assert submitted["parameters"]["Origin"]["Residues"] == [[{"Chain": "A", "SequenceNumber": 308}, {"Chain": "A", "SequenceNumber": 309}]]
    resource = rpc(client, "resources/read", {"uri": f"mole://sessions/{id}/jobs/1/data"})
    assert json.loads(resource["contents"][0]["text"]) == {"Channels": {"Tunnels": [TUNNEL]}}


def test_starting_points(client):
    """A ligand, a known cofactor from its atom, or a point: the job's origin as the website would submit it"""
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    wait_session(client, id)
    origins = []
    used = []
    for number, start in enumerate([{"ligand": "hem"}, {"ligand": "LIG"}, {"point": [-19.28, -23.62, -14.17]}], 1):
        kind = next(iter(start))
        failed, job = tool(client, "compute_tunnels", session_id=id, start=kind, **start)
        assert not failed and job["number"] == number
        used.append(job["starting_point"])
        wait(client, id, number)
        origins.append(client.get(f"/sessions/{id}/jobs/{number}").json()["parameters"]["Origin"])
    assert [o["QueryExpression"] for o in origins[:2]] == ["Atoms('Fe').Inside(Residues('HEM'))", "Residues('LIG')"]
    assert origins[2]["Points"] == [{"X": -19.28, "Y": -23.62, "Z": -14.17}]
    assert used == [{"ligand": "HEM", "query": "Atoms('Fe').Inside(Residues('HEM'))"}, {"ligand": "LIG", "query": "Residues('LIG')"},
                    {"point": [-19.28, -23.62, -14.17]}]


@pytest.mark.parametrize(("start", "message"), [
    ({"start": "ligand"}, "Give the ligand's residue name"),
    ({"start": "ligand", "ligand": "HEM') or Residues('ALA"}, "Give the ligand's residue name"),  # nothing else into the query
    ({"start": "point"}, "Give the point as its coordinates [x, y, z]"),
    ({"start": "point", "point": [1.0, 2.0]}, "Give the point as its coordinates [x, y, z]"),
])
def test_starting_points_refused(client, start, message):
    failed, text = tool(client, "compute_tunnels", session_id="any", **start)
    assert failed and message in text


@pytest.mark.parametrize(("url", "message"), [
    ("http://example.org/1tqn.cif", "Only https URLs are accepted."),
    ("https://localhost/1tqn.cif", "Only public hosts are accepted."),
    ("https://10.0.0.1/1tqn.cif", "Only public hosts are accepted."),
])
def test_only_public_https_urls(url, message):
    with pytest.raises(mcp_server.MoleError, match=message):
        asyncio.run(mcp_server.check_public(url))


def test_the_download_and_the_wait_within_one_call(api, monkeypatch):
    client = api(load="sleep 10")
    async def slow(url: str) -> bytes:
        await asyncio.sleep(2)
        return STRUCTURE
    monkeypatch.setattr(mcp_server, "downloaded", slow)
    monkeypatch.setattr(mcp_server, "DOWNLOAD_SECONDS", 0.1)
    assert tool(client, "load_structure_url", url="https://example.org/1tqn.cif") == (
        True, "Error executing tool load_structure_url: The download took longer than 0.1 s.")

    monkeypatch.setattr(mcp_server, "DOWNLOAD_SECONDS", 5)
    monkeypatch.setattr(mcp_server, "MAX_WAIT_SECONDS", 2.5)  # the download took most of it, the wait the rest
    started = time.monotonic()
    failed, session = tool(client, "load_structure_url", url="https://example.org/1tqn.cif", wait_seconds=30)
    assert time.monotonic() - started < 3.5  # 4.5 s when the wait started after the download
    assert not failed and session["state"] in ("queued", "loading")  # its id returned, the model asks again
