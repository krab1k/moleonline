"""Paths as the C# API matched them, and its static files"""

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
    assert "MOLE" in client.get("/").text  # the documentation page
    assert client.get("/inputs/cofactors.json").json()
    assert client.get("/docs").status_code == 200
    assert client.get("/nothing.txt").status_code == 404
