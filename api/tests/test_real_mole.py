"""A tunnel computation with the real MOLE: set MOLE2 to the mole2 executable, otherwise the test is skipped"""

import os
from pathlib import Path

import pytest

from .conftest import STRUCTURE, upload, wait
from .test_submit import EXAMPLES, submit

MOLE = os.environ.get("MOLE2")


@pytest.mark.skipif(MOLE is None, reason="set MOLE2 to the mole2 executable")
def test_tunnels_of_an_uploaded_structure(api):
    client = api(mole=Path(MOLE))
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    assert wait(client, id, seconds=120)["Status"] == "Initialized"

    assert submit(client, id, (EXAMPLES / "1tqn_point_channels.json").read_text()).status_code == 200  # the example of the documentation
    assert wait(client, id, 1, seconds=120)["Status"] == "Finished"
    data = client.get(f"/Data/{id}?submitId=1").json()
    assert data["Version"] == client.get("/Version").json()["MoleVersion"]  # read from MOLE when the API started
    assert any(data["Channels"].values())  # tunnels, paths or pores
    for format in ("report", "pymol", "vmd", "chimera", "pdb", "molecule"):
        response = client.get(f"/Data/{id}?submitId=1&format={format}")
        assert response.status_code == 200 and len(response.content) > 100, format
    assert client.get(f"/Data/{id}?submitId=0").status_code == 200  # the cavities of the load
    assert client.get(f"/CompInfo/{id}").json()["Submissions"][0]["Status"] == "Finished"
