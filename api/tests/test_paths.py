"""Paths as the former C# API matched them, and the static files"""

from fastapi.testclient import TestClient

from mole_api.main import create_app

from .conftest import STRUCTURE, upload


def test_endpoint_names_in_any_case_and_a_trailing_slash(client):
    response = client.post("/Init/", files={"file": ("1tqn.cif", STRUCTURE)})  # as the web uploads
    assert response.status_code == 200
    id = response.json()["ComputationId"]
    for path in (f"/status/{id}", f"/STATUS/{id}/", f"/Status/{id}"):
        assert client.get(path).json()["ComputationId"] == id
    assert client.get(f"/Status/{id.lower()}").status_code == 404  # computation ids keep their case
    assert client.get("/version/").status_code == 200


def test_static_files(client):
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 307 and home.headers["location"] == "docs"  # relative: also behind nginx's /api/
    assert client.get("/inputs/cofactors.json").json()
    assert client.get("/docs").status_code == 200
    assert client.get("/nothing.txt").status_code == 404


def test_behind_the_proxy_prefix(settings):
    # nginx serves the API under /api/ and strips the prefix; uvicorn runs with --root-path /api and passes the
    # paths with it (/api/docs), which the test client does not: they are written here as uvicorn passes them
    with TestClient(create_app(settings), root_path="/api") as client:
        assert "url: '/api/openapi.json'" in client.get("/api/docs").text  # the documentation asks through the proxy
        assert client.get("/api/openapi.json").json()["servers"] == [{"url": "/api"}]  # so does "Try it out"
        home = client.get("/api/", follow_redirects=False)
        assert home.status_code == 307 and home.headers["location"] == "docs"  # /api/docs, no loop
        assert client.get("/api/status/nope/").status_code == 404 and client.get("/api/version/").status_code == 200
