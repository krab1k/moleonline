"""CORS: the web's pages are served from another site than the API"""

import pytest
from fastapi.testclient import TestClient

from moleonline import storage
from moleonline.main import create_app

from .conftest import STRUCTURE

ORIGIN = {"Origin": "https://moleonline.cz"}


def test_responses_allow_other_sites(client):
    for response in (client.get("/version", headers=ORIGIN), client.get("/sessions/nope", headers=ORIGIN)):
        assert response.headers["access-control-allow-origin"] == "*"
    # the page reads where to poll, when to try again, the names of downloads
    created = client.post("/sessions/upload", files={"file": ("1tqn.cif", STRUCTURE)}, headers=ORIGIN)
    assert created.status_code == 202
    exposed = created.headers["access-control-expose-headers"]
    assert {"Location", "Retry-After", "Content-Disposition"} <= {h.strip() for h in exposed.split(",")}


def test_preflight_of_a_json_request(client):
    preflight = client.options("/sessions", headers=ORIGIN | {"Access-Control-Request-Method": "POST",
                                                              "Access-Control-Request-Headers": "Content-Type"})
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert "POST" in preflight.headers["access-control-allow-methods"]
    assert "content-type" in preflight.headers["access-control-allow-headers"].lower()


def test_unexpected_errors_allow_other_sites(settings, monkeypatch, caplog):
    async def broken(*args, **kwargs):
        raise RuntimeError("broken")
    monkeypatch.setattr(storage, "create", broken)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.post("/sessions", json={"pdb_id": "1tqn"}, headers=ORIGIN)
    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.json()["detail"] == "An unexpected error occurred on the server. Please, try again later."
    assert any(r.exc_info and "broken" in str(r.exc_info[1]) for r in caplog.records)  # logged with its traceback


@pytest.mark.parametrize("origin, allowed", [("https://moleonline.cz", True), ("https://example.org", False)])
def test_origins_from_the_settings(settings, origin, allowed):
    settings = settings.model_copy(update={"cors_origins": ["https://moleonline.cz"]})
    with TestClient(create_app(settings)) as client:
        response = client.get("/version", headers={"Origin": origin})
    assert (response.headers.get("access-control-allow-origin") == origin) == allowed
