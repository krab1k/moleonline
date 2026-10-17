"""GET /CompInfo: the computation and its submissions with their parameters"""

import json

from .conftest import STRUCTURE, upload, wait
from .test_status import written_by_csharp
from .test_submit import EXAMPLES, submit


def test_submissions_with_their_parameters(client):
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    wait(client, id)
    assert client.get(f"/CompInfo/{id}").json() == {"ComputationId": id, "UserStructure": True, "PdbId": "", "AssemblyId": None, "Submissions": []}

    parameters = (EXAMPLES / "1tqn_point_channels.json").read_text()
    submit(client, id, parameters)
    wait(client, id, 1)
    submit(client, id, {"Cavity": {"ProbeRadius": 100}})  # refused, no submission
    info = client.get(f"/CompInfo/{id}").json()
    assert len(info["Submissions"]) == 1
    first = info["Submissions"][0]
    assert {k: first[k] for k in ("SubmitId", "Mode", "Status", "ErrorMsg", "PoresConfig")} == {"SubmitId": 1, "Mode": "Mole", "Status": "Finished", "ErrorMsg": "", "PoresConfig": {}}
    assert first["MoleConfig"]["Cavity"] == json.loads(parameters)["Cavity"]


def test_pore_computation_of_the_csharp_api(client, settings):
    id = written_by_csharp(settings.working_directory)
    (settings.working_directory / id / "2").mkdir()
    (settings.working_directory / id / "2" / "pores.json").write_text(json.dumps({
        "Structure": "/data/x/1tqn.cif", "PdbId": "1tqn", "WorkingDirectory": "/data/x/2", "Membrane": "/data/x/membrane.json",
        "InMembrane": True, "IsBetaBarrel": False, "Chains": ["A"], "ProbeRadius": 13.0, "InteriorThreshold": 0.8}))
    info = client.get(f"/CompInfo/{id}").json()
    assert (info["PdbId"], info["AssemblyId"], info["UserStructure"]) == ("1tqn", "1", False)
    assert info["Submissions"] == [{"SubmitId": 2, "Mode": "Pores", "Status": "Finished", "ErrorMsg": "Chain Z is not in the structure.\n", "MoleConfig": {},
                                    "PoresConfig": {"InMembrane": True, "IsBetaBarrel": False, "Chains": ["A"], "ProbeRadius": 13.0, "InteriorThreshold": 0.8}}]


def test_pores_json_of_an_earlier_version(client, settings):
    id = written_by_csharp(settings.working_directory)
    (settings.working_directory / id / "2").mkdir()
    (settings.working_directory / id / "2" / "pores.json").write_text('{"InMembrane": true, "IsBetaBarel": true, "Chains": [], "ProbeRadius": 13.0, "InteriorThreshold": 0.8}')
    config = client.get(f"/CompInfo/{id}").json()["Submissions"][0]["PoresConfig"]
    assert config == {"InMembrane": True, "IsBetaBarrel": False, "Chains": [], "ProbeRadius": 13.0, "InteriorThreshold": 0.8}  # as the C# API read it


def test_case_of_the_web_and_errors(client):
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    assert client.get(f"/Compinfo/{id}/").status_code == 200  # as the web asks
    assert client.get("/CompInfo/nope").status_code == 404
    client.delete(f"/Delete/{id}")
    assert client.get(f"/CompInfo/{id}").status_code == 410
