"""Errors as problem details (RFC 9457): application/problem+json with type, title, status and detail"""

from fastapi.testclient import TestClient

from moleonline.api.main import create_app
from moleonline.core import storage

from .conftest import STRUCTURE, upload, wait_session

PROBLEM = "application/problem+json"


def test_validation_lists_the_invalid_fields(client):
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    wait_session(client, id)
    body = {"type": "tunnels", "parameters": {"Cavity": {"ProbeRadius": "big"}, "Origin": {"Points": [{"X": "NaN", "Y": 0, "Z": 0}]}}}
    response = client.post(f"/sessions/{id}/jobs", json=body)
    assert response.status_code == 422 and response.headers["content-type"] == PROBLEM
    problem = response.json()
    assert (problem["type"], problem["title"], problem["status"]) == ("about:blank", "Unprocessable Content", 422)
    assert [e["field"] for e in problem["errors"]] == ["parameters.Cavity.ProbeRadius", "parameters.Origin.Points.0.X"]
    assert problem["detail"].startswith("Invalid request. parameters.Cavity.ProbeRadius: ")


def test_other_problems_have_no_fields(client):
    response = client.get("/sessions/nope")
    assert response.headers["content-type"] == PROBLEM
    assert "errors" not in response.json()


def test_errors_of_the_framework_too(client):
    unknown = client.get("/nothing/here")  # no route: also not a static file
    assert unknown.status_code == 404 and unknown.headers["content-type"] == PROBLEM
    assert unknown.json() == {"type": "about:blank", "title": "Not Found", "status": 404, "detail": "Not Found"}
    wrong_method = client.post("/version")
    assert wrong_method.status_code == 405 and wrong_method.headers["content-type"] == PROBLEM
    assert wrong_method.json()["title"] == "Method Not Allowed"
    assert wrong_method.headers["allow"] == "GET, HEAD"  # the exception's headers are kept


def test_documented_as_problems(client):
    description = client.get("/openapi.json").json()
    responses = description["paths"]["/sessions/{session_id}/jobs"]["post"]["responses"]
    for status in ("404", "409", "410", "422", "500", "503"):
        assert responses[status]["content"] == {PROBLEM: {"schema": {"$ref": "#/components/schemas/Problem"}}}, status
    assert "HTTPValidationError" not in description["components"]["schemas"]  # FastAPI's 422 is replaced


def test_unexpected_errors_are_problems_too(settings, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("broken")
    monkeypatch.setattr(storage, "create", broken)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.post("/sessions", json={"pdb_id": "1tqn"})
    assert response.status_code == 500 and response.headers["content-type"] == PROBLEM
    assert response.json()["detail"] == "An unexpected error occurred on the server. Please, try again later."
