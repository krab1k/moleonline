"""GET /sessions/{id}: the session with its jobs and their parameters"""

import json

from .conftest import STRUCTURE, upload, wait, wait_session
from .test_submit import EXAMPLES, submit


def test_jobs_with_their_parameters(client):
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    session = wait_session(client, id)
    assert (session["pdb_id"], session["assembly_id"], session["jobs"]) == (None, None, [])

    parameters = (EXAMPLES / "1tqn_point_channels.json").read_text()
    submit(client, id, parameters)
    wait(client, id, 1)
    submit(client, id, {"Cavity": {"ProbeRadius": 100}})  # refused, no job
    [job] = client.get(f"/sessions/{id}").json()["jobs"]
    assert {k: job[k] for k in ("number", "type", "state", "error")} == {"number": 1, "type": "tunnels", "state": "finished", "error": ""}
    assert job["parameters"]["Cavity"] == json.loads(parameters)["Cavity"]

