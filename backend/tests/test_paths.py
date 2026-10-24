"""The static files, the documentation behind the proxy's prefix"""

from fastapi.testclient import TestClient

from moleonline.main import create_app


def test_static_files(client):
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 307 and home.headers["location"] == "docs"  # relative: also behind nginx's /api/
    assert client.get("/inputs/1tqn_point_channels.json").json()  # the example inputs
    assert client.get("/docs").status_code == 200
    assert client.get("/nothing.txt").status_code == 404


def test_behind_the_proxy_prefix(settings):
    # a proxy serves the API under /api/ and strips the prefix; uvicorn runs with UVICORN_ROOT_PATH=/api and passes
    # the paths with it (/api/docs), which the test client does not: they are written here as uvicorn passes them
    with TestClient(create_app(settings), root_path="/api") as client:
        assert "url: '/api/openapi.json'" in client.get("/api/docs").text  # the documentation asks through the proxy
        assert client.get("/api/openapi.json").json()["servers"] == [{"url": "/api"}]  # so does "Try it out"
        home = client.get("/api/", follow_redirects=False)
        assert home.status_code == 307 and home.headers["location"] == "docs"  # /api/docs, no loop
        assert client.get("/api/sessions/nope").status_code == 404 and client.get("/api/version").status_code == 200
