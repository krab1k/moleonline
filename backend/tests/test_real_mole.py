"""A tunnel computation with the real MOLE: set MOLE2 to the mole2 executable, otherwise the test is skipped"""

import os
from pathlib import Path

import pytest

from .conftest import STRUCTURE, upload, wait, wait_session
from .test_submit import EXAMPLES, submit

MOLE = os.environ.get("MOLE2")


@pytest.mark.skipif(MOLE is None, reason="set MOLE2 to the mole2 executable")
def test_tunnels_of_an_uploaded_structure(api):
    client = api(mole=Path(MOLE))
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    assert wait_session(client, id, seconds=120)["state"] == "ready"

    assert submit(client, id, (EXAMPLES / "1tqn_point_channels.json").read_text()).status_code == 202  # the example of the documentation
    assert wait(client, id, 1, seconds=120)["state"] == "finished"
    data = client.get(f"/sessions/{id}/jobs/1/results/data").json()
    assert data["Version"] == client.get("/version").json()["mole"]  # read from MOLE when the worker started
    assert any(data["Channels"].values())  # tunnels, paths or pores
    for format in ("report", "pymol", "vmd", "chimera", "pdb"):
        response = client.get(f"/sessions/{id}/jobs/1/results/{format}")
        assert response.status_code == 200 and len(response.content) > 100, format
    assert len(client.get(f"/sessions/{id}/structure").content) > 100
    assert client.get(f"/sessions/{id}/data").status_code == 200  # the cavities of the load
    assert client.get(f"/sessions/{id}").json()["jobs"][0]["state"] == "finished"
