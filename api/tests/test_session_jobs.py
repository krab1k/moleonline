"""GET /sessions/{id}: the session with its jobs and their parameters"""

import json

from .conftest import STRUCTURE, upload, wait, wait_session
from .test_status import written_by_csharp
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


def test_pore_job_of_the_csharp_api(client, settings):
    id = written_by_csharp(settings.working_directory)
    (settings.working_directory / id / "2").mkdir()
    (settings.working_directory / id / "2" / "pores.json").write_text(json.dumps({
        "Structure": "/data/x/1tqn.cif", "PdbId": "1tqn", "WorkingDirectory": "/data/x/2", "Membrane": "/data/x/membrane.json",
        "InMembrane": True, "IsBetaBarrel": False, "Chains": ["A"], "ProbeRadius": 13.0, "InteriorThreshold": 0.8}))
    session = client.get(f"/sessions/{id}").json()
    assert (session["pdb_id"], session["assembly_id"], session["state"]) == ("1tqn", "1", "ready")
    assert session["jobs"] == [{"number": 2, "type": "pores", "state": "finished", "error": "Chain Z is not in the structure.\n",
                                "parameters": {"InMembrane": True, "IsBetaBarrel": False, "Chains": ["A"], "ProbeRadius": 13.0, "InteriorThreshold": 0.8},
                                "submitted": None, "started": None, "finished": None}]
    assert client.get(f"/sessions/{id}/jobs/2").json() == session["jobs"][0]


def test_pores_json_of_an_earlier_version(client, settings):
    id = written_by_csharp(settings.working_directory)
    (settings.working_directory / id / "2").mkdir()
    (settings.working_directory / id / "2" / "pores.json").write_text('{"InMembrane": true, "IsBetaBarel": true, "Chains": [], "ProbeRadius": 13.0, "InteriorThreshold": 0.8}')
    parameters = client.get(f"/sessions/{id}").json()["jobs"][0]["parameters"]
    assert parameters == {"InMembrane": True, "IsBetaBarrel": False, "Chains": [], "ProbeRadius": 13.0, "InteriorThreshold": 0.8}  # as the C# API read it
