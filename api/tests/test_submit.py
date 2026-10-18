"""POST /Submit/Mole: tunnel computations with a fake MOLE"""

import json
import time
import zipfile
from xml.etree import ElementTree as ET

import pytest

from mole_api.main import STATIC_FILES

from .conftest import MOLE_VERSION, STRUCTURE, upload, wait, wait_for_file
from .test_iplog import logged
from .test_runner import UNTIL_GO

EXAMPLES = STATIC_FILES / "inputs"
RESULTS = 'mkdir -p "$(dirname "$1")/json" && echo "{}" > "$(dirname "$1")/json/data.json"'  # as MOLE writes them


def loaded(client) -> str:
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    assert wait(client, id)["Status"] == "Initialized"
    return id


def submit(client, id: str, parameters="{}"):
    return client.post(f"/Submit/Mole/{id}", content=parameters if isinstance(parameters, str) else json.dumps(parameters),
                       headers={"Content-Type": "application/json"})


def test_computation_runs_and_finishes(api):
    client = api(run=RESULTS)
    id = loaded(client)
    response = submit(client, id)
    assert response.status_code == 200
    assert response.json() == {"ComputationId": id, "SubmitId": 1, "Status": "Running", "ErrorMsg": ""}  # the load's unit
    assert wait(client, id, 1)["Status"] == "Finished"

    folder = client.app.state.settings.working_directory / id / "1"
    with zipfile.ZipFile(folder / "report.zip") as report:
        assert sorted(report.namelist()) == ["1tqn.cif", "json/data.json"]
    saved = json.loads((folder / "mole.json").read_text())
    assert saved["Cavity"] == {"IgnoreHETAtoms": False, "IgnoreHydrogens": False, "InteriorThreshold": 1.1, "ProbeRadius": 5.0}
    assert saved["Origin"] == {"Points": None, "QueryExpression": None, "Residues": None}

    unit = json.loads((folder.parent / "_object.json").read_text())["ComputationUnits"][0]
    assert unit["Submitted"] <= unit["Started"] <= unit["Finished"]
    assert unit["MoleVersion"] == MOLE_VERSION
    assert submit(client, id).json()["SubmitId"] == 2


def xml(client, id: str, submit_id: int) -> ET.Element:
    return ET.parse(client.app.state.settings.working_directory / id / str(submit_id) / "input.xml").getroot()


def test_input_of_the_examples(client):
    id = loaded(client)
    folder = client.app.state.settings.working_directory / id

    submit(client, id, (EXAMPLES / "1tqn_residue_channels.json").read_text())
    root = xml(client, id, 1)
    assert root.findtext("Input") == str(folder / "1tqn.cif") and root.find("Input").get("SpecificChains") == ""
    assert root.findtext("WorkingDirectory") == str(folder / "1")
    assert root.find("Params/Cavity").attrib == {"ProbeRadius": "9", "InteriorThreshold": "1.25", "IgnoreHETAtoms": "0", "IgnoreHydrogens": "0"}
    assert root.find("Params/Tunnel").attrib == {"BottleneckRadius": "1.25", "BottleneckTolerance": "3", "MaxTunnelSimilarity": "0.7",
                                                 "OriginRadius": "5", "SurfaceCoverRadius": "10", "WeightFunction": "VoronoiScale", "UseCustomExitsOnly": "0"}
    assert root.find("Export/Types").attrib == {"Cavities": "0", "Tunnels": "1", "PoresAuto": "0", "PoresMerged": "0", "PoresUser": "0"}
    assert root.find("Export/PyMol").attrib == {"PDBId": "1tqn", "SurfaceType": "Spheres"}
    assert [r.attrib for r in root.findall("Origins/Origin/Residue")] == [{"SequenceNumber": "308", "Chain": "A"}, {"SequenceNumber": "309", "Chain": "A"}]
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
    ("not json", "Input format error"),
    ("", "Input format error"),
    ({"Cavity": {"ProbeRadius": 100}}, "Invalid parameters. Probe radius must be between 1.4 and 45."),
    ({"Tunnel": {"WeightFunction": "x", "MaxTunnelSimilarity": 2}}, "Invalid parameters. Max tunnel similarity must be between 0 and 1. Weight function must be one of"),
    ({"Input": {"ReadAllModels": True}}, "Input format error. Input.ReadAllModels: Extra inputs are not permitted"),  # only the first model is used
    ({"Cavity": {"ProbeRadius": "big"}}, "Input format error. Cavity.ProbeRadius:"),
    ({"Origin": {"Points": [{"X": "NaN", "Y": 0, "Z": 0}]}}, "Input format error. Origin.Points.0.X: Input should be a finite number"),
    ('{"CustomExits": {"Points": [{"X": 0, "Y": Infinity, "Z": 0}]}}', "Input format error. CustomExits.Points.0.Y: Input should be a finite number"),
    ('{"Cavity": {"ProbeRadius": NaN}}', "Input format error. Cavity.ProbeRadius: Input should be a finite number"),
])
def test_invalid_parameters_are_400(client, settings, parameters, message):
    id = loaded(client)
    response = submit(client, id, parameters)
    assert response.status_code == 400
    assert response.json()["ComputationId"] == id and response.json()["Status"] == "Error"
    assert response.json()["ErrorMsg"].startswith(message)
    assert logged(settings)[-1] == ("Submit", "testclient", "Mole", id)  # refused ones too


def test_extra_fields_of_the_web_are_ignored(client):
    id = loaded(client)
    parameters = {"Tunnel": {"UseCustomExitsOnly": False}, "Origin": {"Residues": [[{"Chain": "A", "SequenceNumber": 1, "OperatorName": "1_555", "Name": "ALA"}]]}}
    assert submit(client, id, parameters).status_code == 200


def test_busy_computation_refuses_and_stops(api):
    client = api(run=UNTIL_GO)
    id = loaded(client)
    submit(client, id)
    wait_for_file(client.app.state.settings.working_directory / id / "1" / "started")

    busy = submit(client, id)
    assert busy.status_code == 409 and "still running" in busy.json()["ErrorMsg"]
    killed = client.post(f"/Kill/{id}")
    assert killed.status_code == 200
    assert killed.json() == {"ComputationId": id, "SubmitId": 1, "Status": "Aborted", "ErrorMsg": ""}
    time.sleep(0.2)
    assert client.get(f"/Status/{id}?submitId=1").json()["Status"] == "Aborted"  # the run did not write over it

    again = submit(client, id)  # a stopped computation does not block the structure
    assert again.status_code == 200 and again.json()["SubmitId"] == 2


def test_all_slots_busy_is_503(api):
    client = api(run=UNTIL_GO, slots=1)
    first, second = loaded(client), loaded(client)
    submit(client, first)
    response = submit(client, second)
    assert response.status_code == 503 and "heavy load" in response.json()["ErrorMsg"]


@pytest.mark.parametrize("load, status, message", [
    ("exit 3", 409, "could not be loaded"),
    (UNTIL_GO, 409, "still being initialized"),
])
def test_structure_not_loaded_is_409(api, load, status, message):
    client = api(load=load)
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    if load == "exit 3":
        wait(client, id)
    response = submit(client, id)
    assert response.status_code == status and message in response.json()["ErrorMsg"]


@pytest.mark.parametrize("run, message", [
    ("exit 7", "The computation failed with exit code 7."),
    ("echo 'Invalid structure' >&2", "Invalid structure\n"),
])
def test_failed_computation_is_an_error(api, run, message):
    client = api(run=run)
    id = loaded(client)
    submit(client, id)
    status = wait(client, id, 1)
    assert status["Status"] == "Error" and status["ErrorMsg"] == message
    assert submit(client, id).status_code == 200  # the structure is still loaded


def test_unknown_and_deleted_computation(client):
    assert submit(client, "nope").status_code == 404
    id = loaded(client)
    client.delete(f"/Delete/{id}")
    assert submit(client, id).status_code == 410
