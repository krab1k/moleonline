"""POST /Submit/Pores: the pores tool, then MOLE, both fake here"""

import json
import zipfile

import pytest

from .conftest import STRUCTURE, upload, wait
from .test_iplog import logged
from .test_pdbe import ASSEMBLIES, XML, FakePDBe, assembly
from .test_runner import STARTED

# MOLE's pore along the membrane normal, or none
PORE = 'mkdir -p "$(dirname "$1")/json" && echo \'{"Channels": {"Paths": [{"Id": "1"}]}}\' > "$(dirname "$1")/json/data.json"'
NO_PORE = 'mkdir -p "$(dirname "$1")/json" && echo \'{"Channels": {"Paths": []}}\' > "$(dirname "$1")/json/data.json"'


def loaded(client) -> str:
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    assert wait(client, id)["Status"] == "Initialized"
    return id


def submit(client, id: str, parameters="{}"):
    return client.post(f"/Submit/Pores/{id}", content=parameters if isinstance(parameters, str) else json.dumps(parameters),
                       headers={"Content-Type": "application/json"})


def test_pore_is_computed(api, settings):
    client = api(run=PORE)
    id = loaded(client)
    response = submit(client, id, {"InMembrane": True, "Chains": ["A", "A_2"]})
    assert response.status_code == 200
    assert response.json() == {"ComputationId": id, "SubmitId": 1, "Status": "Running", "ErrorMsg": ""}
    assert wait(client, id, 1)["Status"] == "Finished"

    folder = settings.working_directory / id
    assert json.loads((folder / "1" / "pores.json").read_text()) == {
        "Structure": str(folder / "1tqn.cif"), "PdbId": None, "WorkingDirectory": str(folder / "1"), "Membrane": str(folder / "membrane.json"),
        "Memembed": str(settings.memembed), "OpmCache": str(settings.opm_cache), "InMembrane": True, "IsBetaBarrel": False, "Chains": ["A", "A_2"], "ProbeRadius": 13.0, "InteriorThreshold": 0.8}
    with zipfile.ZipFile(folder / "1" / "report.zip") as report:
        assert sorted(report.namelist()) == ["1tqn.cif", "json/data.json"]
    submission = client.get(f"/CompInfo/{id}").json()["Submissions"][0]
    assert (submission["Mode"], submission["MoleConfig"]) == ("Pores", {})
    assert submission["PoresConfig"] == {"InMembrane": True, "IsBetaBarrel": False, "Chains": ["A", "A_2"], "ProbeRadius": 13.0, "InteriorThreshold": 0.8}
    assert logged(settings)[-1] == ("Submit", "testclient", "Pores", id)


def test_tools_run_one_after_another(api, settings):
    # the pores tool writes MOLE's input next to its own; MOLE gets it
    client = api(pores=f'{STARTED}; touch "$(dirname "$1")/input.xml"', run='[ -f "$(dirname "$1")/started" ] && ' + PORE)
    id = loaded(client)
    submit(client, id)
    assert wait(client, id, 1)["Status"] == "Finished"


def test_warning_of_the_pores_tool_is_no_error(api, caplog):
    # the pores tool fails with its exit code (unlike MOLE), a warning on stderr (e.g. numpy's) does not fail the run;
    # it is logged, in one line
    client = api(pores="printf 'RuntimeWarning: Mean of empty slice\\n  return x.mean()\\n' >&2", run=PORE)
    id = loaded(client)
    submit(client, id)
    assert wait(client, id, 1)["Status"] == "Finished"
    [record] = [r for r in caplog.records if "stderr" in r.getMessage()]
    assert record.levelname == "WARNING"
    assert record.getMessage().endswith(f"/{id}/1/pores.json succeeded with output on stderr: RuntimeWarning: Mean of empty slice return x.mean()")


def test_opm_membrane_for_the_preferred_assembly(client, settings):
    FakePDBe(client, {XML: ASSEMBLIES, assembly("1"): STRUCTURE})
    id = client.post("/Init/1tqn").json()["ComputationId"]
    wait(client, id)
    submit(client, id, {"ProbeRadius": 10, "InteriorThreshold": 1})
    saved = json.loads((settings.working_directory / id / "1" / "pores.json").read_text())
    assert (saved["PdbId"], saved["ProbeRadius"], saved["InteriorThreshold"]) == ("1tqn", 10.0, 1.0)


@pytest.mark.parametrize("pores, run, message", [
    ("exit 2", PORE, "The computation failed with exit code 2."),  # MOLE does not run
    ("echo 'No membrane found' >&2; exit 1", PORE, "No membrane found\n"),  # the reason, not the exit code
    ("exit 0", NO_PORE, "No pore was found along the membrane normal."),
    ("exit 0", "exit 0", "MOLE did not produce any result."),
])
def test_failed_pore_computation(api, settings, pores, run, message):
    client = api(pores=pores, run=run)
    id = loaded(client)
    submit(client, id)
    status = wait(client, id, 1)
    assert status["Status"] == "Error" and status["ErrorMsg"] == message
    assert not (settings.working_directory / id / "1" / "report.zip").exists()


@pytest.mark.parametrize("parameters, message", [
    ({"IsBetaStructure": True}, "Input format error. IsBetaStructure: Extra inputs are not permitted"),  # misspelled
    ({"ProbeRadius": 100, "InteriorThreshold": 0.1}, "Invalid parameters. Probe radius must be between 1.4 and 45. Interior threshold must be between 0.3 and 3."),
    ({"Chains": ["A", "B C"]}, "Invalid parameters. Chain names must not be empty or contain spaces."),
    ({"Chains": [""]}, "Invalid parameters. Chain names must not be empty or contain spaces."),
    ('{"ProbeRadius": NaN}', "Input format error. ProbeRadius: Input should be a finite number"),
    ("not json", "Input format error"),
])
def test_invalid_parameters_are_400(client, settings, parameters, message):
    id = loaded(client)
    response = submit(client, id, parameters)
    assert response.status_code == 400
    assert response.json()["ErrorMsg"].startswith(message)
    assert logged(settings)[-1] == ("Submit", "testclient", "Pores", id)


def test_the_web_request(client):
    id = loaded(client)
    body = {"InMembrane": False, "IsBetaBarrel": True, "Chains": None, "InteriorThreshold": None, "ProbeRadius": None}  # as the web sends it
    assert submit(client, id, body).status_code == 200


def test_unknown_and_deleted_computation(client):
    assert submit(client, "nope").status_code == 404
    id = loaded(client)
    client.delete(f"/Delete/{id}")
    assert submit(client, id).status_code == 410
