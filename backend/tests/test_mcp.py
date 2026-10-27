"""/mcp: the MCP server, its tools calling the API in the process"""

import asyncio
import json
import time

import pytest

from moleonline.api import mcp_server

from .conftest import STRUCTURE, upload, wait_session

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
    assert {t["name"] for t in rpc(client, "tools/list")["tools"]} == {
        "load_structure", "load_structure_url", "get_session", "compute_tunnels", "compute_pore", "get_job"}


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
        False, {"session_id": id, "number": 1, "state": "queued"})
    failed, job = tool(client, "get_job", session_id=id, number=1, wait_seconds=10)
    assert not failed and job["state"] == "finished"
    assert job["channels"] == [{"type": "Tunnels", "id": "1", "length": 12.3, "bottleneck_radius": 1.1, "charge": 1, "hydropathy": -0.5,
                                "polarity": 10.0, "lining_residues": ["THR 309 A", "GLY 310 A"]}]
    assert job["web"] == f"http://testserver/online/{id}/1"  # from the request without PUBLIC_URL
    assert job["downloads"]["pymol"] == f"http://testserver/sessions/{id}/jobs/1/results/pymol"
    client.app.state.settings.public_url = "https://moleonline.example.org/"  # what users see, not the proxy's request
    assert tool(client, "get_job", session_id=id, number=1)[1]["web"] == f"https://moleonline.example.org/online/{id}/1"

    [submitted] = client.get(f"/sessions/{id}").json()["jobs"]  # a job like any other
    assert submitted["parameters"]["Origin"]["Residues"] == [[{"Chain": "A", "SequenceNumber": 308}, {"Chain": "A", "SequenceNumber": 309}]]
    resource = rpc(client, "resources/read", {"uri": f"mole://sessions/{id}/jobs/1/data"})
    assert json.loads(resource["contents"][0]["text"]) == {"Channels": {"Tunnels": [TUNNEL]}}


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
