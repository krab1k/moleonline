"""POST /sessions/{id}/jobs: tunnel jobs with a fake MOLE"""

import json
import time
import zipfile
from xml.etree import ElementTree as ET

import pytest

from moleonline.main import STATIC_FILES

from .conftest import MOLE_VERSION, STRUCTURE, query, upload, wait, wait_for_file, wait_session
from .test_requests import logged
from .test_runner import UNTIL_GO

EXAMPLES = STATIC_FILES / "inputs"
RESULTS = 'mkdir -p "$(dirname "$1")/json" && echo "{}" > "$(dirname "$1")/json/data.json"'  # as MOLE writes them


def loaded(client) -> str:
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    assert wait_session(client, id)["state"] == "ready"
    return id


def submit(client, id: str, parameters="{}", type: str = "tunnels"):
    """A job with these parameters: an object, or the text of the parameters as sent (also not JSON)"""
    text = parameters if isinstance(parameters, str) else json.dumps(parameters)
    return client.post(f"/sessions/{id}/jobs", content=f'{{"type": "{type}", "parameters": {text}}}',
                       headers={"Content-Type": "application/json"})


def test_job_runs_and_finishes(api):
    client = api(run=RESULTS)
    id = loaded(client)
    response = submit(client, id)
    assert response.status_code == 202
    assert response.headers["location"] == f"/sessions/{id}/jobs/1"
    job = response.json()
    assert (job["number"], job["type"], job["state"], job["error"]) == (1, "tunnels", "queued", "")  # numbered from 1
    assert job["parameters"]["Cavity"]["ProbeRadius"] == 5.0  # stored with the defaults
    done = wait(client, id, 1)
    assert done["state"] == "finished"
    assert done["submitted"] <= done["started"] <= done["finished"]
    assert client.get(f"/sessions/{id}").json()["jobs"] == [done]

    folder = client.app.state.settings.working_directory / id / "jobs" / "1"
    with zipfile.ZipFile(folder / "report.zip") as report:
        assert sorted(report.namelist()) == ["1tqn.cif", "json/data.json"]
    [(parameters, version)] = query(f"SELECT parameters, mole_version FROM jobs WHERE session_id = '{id}'")  # stored with it
    assert parameters["Cavity"] == {"IgnoreHETAtoms": False, "IgnoreHydrogens": False, "InteriorThreshold": 1.1, "ProbeRadius": 5.0}
    assert parameters["Origin"] == {"Points": None, "QueryExpression": None, "Residues": None}
    assert version == MOLE_VERSION
    assert submit(client, id).json()["number"] == 2


def xml(client, id: str, submit_id: int) -> ET.Element:
    """MOLE's input of the job, written when it starts"""
    wait(client, id, submit_id)
    return ET.parse(client.app.state.settings.working_directory / id / "jobs" / str(submit_id) / "input.xml").getroot()


def test_input_of_the_examples(client):
    id = loaded(client)
    folder = client.app.state.settings.working_directory / id

    submit(client, id, (EXAMPLES / "1tqn_residue_channels.json").read_text())
    root = xml(client, id, 1)
    assert root.findtext("Input") == str(folder / "1tqn.cif") and root.find("Input").get("SpecificChains") == ""
    assert root.findtext("WorkingDirectory") == str(folder / "jobs" / "1")
    assert root.find("Params/Cavity").attrib == {"ProbeRadius": "9", "InteriorThreshold": "1.25", "IgnoreHETAtoms": "0",
                                                 "IgnoreHydrogens": "0"}
    assert root.find("Params/Tunnel").attrib == {"BottleneckRadius": "1.25", "BottleneckTolerance": "3", "MaxTunnelSimilarity": "0.7",
                                                 "OriginRadius": "5", "SurfaceCoverRadius": "10", "WeightFunction": "VoronoiScale",
                                                 "UseCustomExitsOnly": "0"}
    assert root.find("Export/Types").attrib == {"Cavities": "0", "Tunnels": "1", "PoresAuto": "0", "PoresMerged": "0", "PoresUser": "0"}
    assert root.find("Export/PyMol").attrib == {"PDBId": "1tqn", "SurfaceType": "Spheres"}
    assert [r.attrib for r in root.findall("Origins/Origin/Residue")] == [{"SequenceNumber": "308", "Chain": "A"},
                                                                          {"SequenceNumber": "309", "Chain": "A"}]
    assert root.find("CustomExits") is None
    wait(client, id, 1)

    submit(client, id, (EXAMPLES / "1tqn_custom_exit_tunnel.json").read_text())  # an origin and exits: tunnels to the exits
    root = xml(client, id, 2)
    assert root.find("Params/Tunnel").get("UseCustomExitsOnly") == "1"
    assert root.find("Origins/Origin/Point").attrib == {"X": "-19.28", "Y": "-23.62", "Z": "-14.17"}
    assert root.find("CustomExits/Exit/Point").attrib == {"X": "-23", "Y": "-7.4", "Z": "2.5"}
    wait(client, id, 2)

    submit(client, id, {"CustomExits": {"QueryExpression": "Residues('HEM')"}, "NonActiveResidues": [{"Chain": "A", "SequenceNumber": 1}],
                        "QueryFilter": "Atoms('Fe')", "Origin": None})  # exits without an origin: pores between them
    root = xml(client, id, 3)
    assert root.find("Export/Types").attrib == {"Cavities": "0", "Tunnels": "0", "PoresAuto": "0", "PoresMerged": "0", "PoresUser": "1"}
    assert root.find("Origins").attrib == {"Auto": "1"}
    assert root.findtext("CustomExits/Exit/Query") == "Residues('HEM')"
    assert root.find("NonActiveParts/Residue").attrib == {"SequenceNumber": "1", "Chain": "A"}
    assert root.findtext("NonActiveParts/Query") == "Atoms('Fe')"


@pytest.mark.parametrize("parameters, message", [
    ("not json", "Invalid request"),
    ("", "Invalid request"),
    ({"Cavity": {"ProbeRadius": 100}}, "Invalid parameters. Probe radius must be between 1.4 and 45."),
    ({"Tunnel": {"WeightFunction": "x", "MaxTunnelSimilarity": 2}},
     "Invalid parameters. Max tunnel similarity must be between 0 and 1. Weight function must be one of"),
    # only the first model is used
    ({"Input": {"ReadAllModels": True}}, "Invalid request. parameters.Input.ReadAllModels: Extra inputs are not permitted"),
    ({"Cavity": {"ProbeRadius": "big"}}, "Invalid request. parameters.Cavity.ProbeRadius:"),
    ({"Origin": {"Points": [{"X": "NaN", "Y": 0, "Z": 0}]}},
     "Invalid request. parameters.Origin.Points.0.X: Input should be a finite number"),
    ('{"CustomExits": {"Points": [{"X": 0, "Y": Infinity, "Z": 0}]}}',
     "Invalid request. parameters.CustomExits.Points.0.Y: Input should be a finite number"),
    ('{"Cavity": {"ProbeRadius": NaN}}', "Invalid request. parameters.Cavity.ProbeRadius: Input should be a finite number"),
])
def test_invalid_parameters_are_422(client, settings, parameters, message):
    id = loaded(client)
    response = submit(client, id, parameters)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"].startswith(message)
    assert client.get(f"/sessions/{id}").json()["jobs"] == []
    # refused ones too, unless the framework refused them before the endpoint
    submitted = ("Submit", "testclient", "Mole", id) in logged(settings)
    assert submitted == message.startswith("Invalid parameters")


@pytest.mark.parametrize("body", [
    '{"parameters": {}}',  # no type
    '{"type": "cavities", "parameters": {}}',
    '{"type": [], "parameters": {}}',  # not a string
    '{"type": {}, "parameters": {}}',
    '{"type": "tunnels", "parameters": {}, "extra": 1}',
    '[]',
])
def test_invalid_job_is_422(client, settings, body):
    id = loaded(client)
    response = client.post(f"/sessions/{id}/jobs", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["detail"].startswith("Invalid request")
    assert [line for line in logged(settings) if line[0] == "Submit"] == []  # refused before the endpoint


def test_extra_fields_of_the_web_are_ignored(client):
    id = loaded(client)
    parameters = {"Tunnel": {"UseCustomExitsOnly": False},
                  "Origin": {"Residues": [[{"Chain": "A", "SequenceNumber": 1, "OperatorName": "1_555", "Name": "ALA"}]]}}
    assert submit(client, id, parameters).status_code == 202


def test_busy_session_refuses_and_the_job_is_cancelled(api):
    client = api(run=UNTIL_GO)
    id = loaded(client)
    submit(client, id)
    wait_for_file(client.app.state.settings.working_directory / id / "jobs" / "1" / "started")

    assert client.get(f"/sessions/{id}/jobs/1").json()["state"] == "running"
    busy = submit(client, id)
    assert busy.status_code == 409 and "has not finished yet" in busy.json()["detail"]
    cancelled = client.post(f"/sessions/{id}/jobs/1/cancel")
    assert cancelled.status_code == 200
    assert (cancelled.json()["state"], cancelled.json()["error"]) == ("cancelled", "")
    time.sleep(0.2)
    assert client.get(f"/sessions/{id}/jobs/1").json()["state"] == "cancelled"  # the run did not write over it
    again = client.post(f"/sessions/{id}/jobs/1/cancel")
    assert again.status_code == 409 and again.json()["detail"] == "The job is cancelled, only a queued or running job can be cancelled."

    second = submit(client, id)  # a cancelled job does not block the structure
    assert second.status_code == 202 and second.json()["number"] == 2
    assert client.post(f"/sessions/{id}/jobs/1/cancel").status_code == 409  # not the running one


def test_finished_job_cannot_be_cancelled_and_unknown_jobs_are_404(api):
    client = api(run=RESULTS)
    id = loaded(client)
    submit(client, id)
    assert wait(client, id, 1)["state"] == "finished"
    assert client.post(f"/sessions/{id}/jobs/1/cancel").status_code == 409
    for response in (client.get(f"/sessions/{id}/jobs/2"), client.post(f"/sessions/{id}/jobs/2/cancel"),
                     client.get(f"/sessions/{id}/jobs/0")):
        assert response.status_code == 404


def test_jobs_wait_in_the_queue_until_it_is_full(api):
    client = api(run=UNTIL_GO, slots=1, queued=1)
    first, second, third = loaded(client), loaded(client), loaded(client)
    folder = client.app.state.settings.working_directory
    submit(client, first)
    wait_for_file(folder / first / "jobs" / "1" / "started")
    waiting = submit(client, second)
    assert waiting.status_code == 202 and waiting.json()["state"] == "queued"  # all slots busy: it waits
    full = submit(client, third)
    assert full.status_code == 503 and full.headers["retry-after"] == "60"
    assert full.json()["detail"] == "The server is busy, its queue is full. Please, try again later."
    assert client.get(f"/sessions/{third}").json()["jobs"] == []
    time.sleep(0.2)
    assert client.get(f"/sessions/{second}/jobs/1").json()["started"] is None  # still waiting

    (folder / first / "jobs" / "1" / "go").touch()  # the slot is free: the queued job runs
    assert wait(client, first, 1)["state"] == "finished"
    wait_for_file(folder / second / "jobs" / "1" / "started")
    assert client.get(f"/sessions/{second}/jobs/1").json()["state"] == "running"
    (folder / second / "jobs" / "1" / "go").touch()
    assert wait(client, second, 1)["state"] == "finished"
    assert submit(client, third).status_code == 202  # room again


def test_queued_job_is_cancelled_before_it_runs(api):
    client = api(run=UNTIL_GO, slots=1)
    first, second = loaded(client), loaded(client)
    folder = client.app.state.settings.working_directory
    submit(client, first)
    wait_for_file(folder / first / "jobs" / "1" / "started")
    submit(client, second)
    cancelled = client.post(f"/sessions/{second}/jobs/1/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled" and cancelled.json()["started"] is None
    (folder / first / "jobs" / "1" / "go").touch()
    assert wait(client, first, 1)["state"] == "finished"
    time.sleep(0.3)  # the slot is free, the cancelled job must not start
    assert not (folder / second / "jobs" / "1" / "started").exists()
    assert submit(client, second).json()["number"] == 2  # the session takes jobs again


@pytest.mark.parametrize("load, status, message", [
    ("exit 3", 409, "could not be loaded"),
    (UNTIL_GO, 409, "still being loaded"),
])
def test_structure_not_loaded_is_409(api, load, status, message):
    client = api(load=load)
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    if load == "exit 3":
        wait_session(client, id)
    response = submit(client, id)
    assert response.status_code == status and message in response.json()["detail"]


@pytest.mark.parametrize("run, message", [
    ("exit 7", "The computation failed with exit code 7."),
    ("echo 'Invalid structure' >&2", "Invalid structure\n"),
])
def test_failed_job(api, run, message):
    client = api(run=run)
    id = loaded(client)
    submit(client, id)
    job = wait(client, id, 1)
    assert job["state"] == "failed" and job["error"] == message
    assert submit(client, id).status_code == 202  # the structure is still loaded


def test_unknown_and_deleted_session(client):
    assert submit(client, "nope").status_code == 404
    id = loaded(client)
    client.delete(f"/sessions/{id}")
    assert submit(client, id).status_code == 410


def test_job_after_one_whose_transaction_failed(api):
    """The folder of a job is made before its transaction commits, and left when it fails; the next job gets its
    number and folder"""
    client = api(run=RESULTS)
    id = loaded(client)
    (client.app.state.settings.working_directory / id / "jobs" / "1").mkdir(parents=True)

    response = submit(client, id)
    assert response.status_code == 202 and response.json()["number"] == 1
    assert wait(client, id, 1)["state"] == "finished"
