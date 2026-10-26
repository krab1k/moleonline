"""Pore jobs: the pores tool, then MOLE, both fake here"""

import json
import zipfile

import pytest

from .conftest import STRUCTURE, upload, wait, wait_session
from .test_pdbe import ASSEMBLIES, XML, FakePDBe, assembly
from .test_requests import logged
from .test_runner import STARTED
from .test_submit import submit as submit_job

# MOLE's pore along the membrane normal, or none
PORE = 'mkdir -p "$(dirname "$1")/json" && echo \'{"Channels": {"Paths": [{"Id": "1"}]}}\' > "$(dirname "$1")/json/data.json"'
NO_PORE = 'mkdir -p "$(dirname "$1")/json" && echo \'{"Channels": {"Paths": []}}\' > "$(dirname "$1")/json/data.json"'


def loaded(client) -> str:
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    assert wait_session(client, id)["state"] == "ready"
    return id


def submit(client, id: str, parameters="{}"):
    return submit_job(client, id, parameters, type="pores")


def test_pore_is_computed(api, settings):
    client = api(run=PORE)
    id = loaded(client)
    response = submit(client, id, {"InMembrane": True, "Chains": ["A", "A_2"]})
    assert response.status_code == 202
    assert (response.json()["number"], response.json()["type"], response.json()["state"]) == (1, "pores", "queued")
    assert wait(client, id, 1)["state"] == "finished"

    folder, scratch = settings.working_directory / id, settings.scratch_directory / id
    assert json.loads((scratch / "1.json").read_text()) == {  # as the fake pores tool kept it
        "Structure": str(folder / "1tqn.cif"), "PdbId": None, "WorkingDirectory": str(scratch / "1"),
        "Membrane": str(scratch / "1" / "membrane.json"), "Memembed": str(settings.memembed), "OpmCache": str(settings.opm_cache),
        "InMembrane": True, "IsBetaBarrel": False, "Chains": ["A", "A_2"], "ProbeRadius": 13.0, "InteriorThreshold": 0.8}
    with zipfile.ZipFile(folder / "jobs" / "1" / "report.zip") as report:
        assert sorted(report.namelist()) == ["1tqn.cif", "json/data.json"]
    [job] = client.get(f"/sessions/{id}").json()["jobs"]
    assert job["type"] == "pores"
    assert job["parameters"] == {"InMembrane": True, "IsBetaBarrel": False, "Chains": ["A", "A_2"], "ProbeRadius": 13.0,
                                 "InteriorThreshold": 0.8}
    assert logged(settings)[-1] == ("Submit", "testclient", "Pores", id)


def test_tools_run_one_after_another(api, settings):
    # the pores tool writes MOLE's input next to its own; MOLE gets it
    client = api(pores=f'{STARTED}; touch "$(dirname "$1")/input.xml"', run='[ -f "$(dirname "$1")/started" ] && ' + PORE)
    id = loaded(client)
    submit(client, id)
    assert wait(client, id, 1)["state"] == "finished"


def test_warning_of_the_pores_tool_is_no_error(api, caplog):
    # the pores tool fails with its exit code (unlike MOLE), a warning on stderr (e.g. numpy's) does not fail the run;
    # it is logged, in one line
    client = api(pores="printf 'RuntimeWarning: Mean of empty slice\\n  return x.mean()\\n' >&2", run=PORE)
    id = loaded(client)
    submit(client, id)
    assert wait(client, id, 1)["state"] == "finished"
    [record] = [r for r in caplog.records if "stderr" in r.getMessage()]
    assert record.levelname == "WARNING"
    assert record.getMessage().endswith(f"/{id}/1/pores.json succeeded with output on stderr: "
                                        "RuntimeWarning: Mean of empty slice return x.mean()")


def test_opm_membrane_for_the_preferred_assembly(client, settings):
    FakePDBe(client, {XML: ASSEMBLIES, assembly("1"): STRUCTURE})
    id = client.post("/sessions", json={"pdb_id": "1tqn"}).json()["id"]
    wait_session(client, id)
    submit(client, id, {"ProbeRadius": 10, "InteriorThreshold": 1})
    wait(client, id, 1)
    saved = json.loads((settings.scratch_directory / id / "1.json").read_text())
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
    job = wait(client, id, 1)
    assert job["state"] == "failed" and job["error"] == message
    assert not (settings.working_directory / id / "jobs" / "1" / "report.zip").exists()


@pytest.mark.parametrize("parameters, message", [
    ({"IsBetaStructure": True}, "Invalid request. parameters.IsBetaStructure: Extra inputs are not permitted"),  # misspelled
    ({"ProbeRadius": 100, "InteriorThreshold": 0.1},
     "Invalid parameters. Probe radius must be between 1.4 and 45. Interior threshold must be between 0.3 and 3."),
    ({"Chains": ["A", "B C"]}, "Invalid parameters. Chain names must not be empty or contain spaces."),
    ({"Chains": [""]}, "Invalid parameters. Chain names must not be empty or contain spaces."),
    ('{"ProbeRadius": NaN}', "Invalid request. parameters.ProbeRadius: Input should be a finite number"),
    ("not json", "Invalid request"),
])
def test_invalid_parameters_are_422(client, settings, parameters, message):
    id = loaded(client)
    response = submit(client, id, parameters)
    assert response.status_code == 422
    assert response.json()["detail"].startswith(message)
    submitted = ("Submit", "testclient", "Pores", id) in logged(settings)
    assert submitted == message.startswith("Invalid parameters")  # not one refused before the endpoint


def test_the_web_request(client):
    id = loaded(client)
    # as the web sends it
    body = {"InMembrane": False, "IsBetaBarrel": True, "Chains": None, "InteriorThreshold": None, "ProbeRadius": None}
    assert submit(client, id, body).status_code == 202
    assert wait(client, id, 1)["parameters"] == {  # null: the default
        "InMembrane": False, "IsBetaBarrel": True, "Chains": [], "ProbeRadius": 13.0, "InteriorThreshold": 0.8}


def test_unknown_and_deleted_session(client):
    assert submit(client, "nope").status_code == 404
    id = loaded(client)
    client.delete(f"/sessions/{id}")
    assert submit(client, id).status_code == 410


def test_membrane_of_each_pore_job(api):
    # the fake pores tool writes the job's number as its membrane, next to pores.json, where the API tells it to
    client = api(pores='d="$(dirname "$1")"; echo "[$(basename "$d")]" > "$d/membrane.json"', run=PORE)
    id = loaded(client)
    submit(client, id)
    assert wait(client, id, 1)["state"] == "finished"
    submit(client, id)
    assert wait(client, id, 2)["state"] == "finished"
    assert client.get(f"/sessions/{id}/jobs/1/results/membrane").json() == [1]  # not replaced by the second job's
    assert client.get(f"/sessions/{id}/jobs/2/results/membrane").json() == [2]


def test_membrane_when_no_pore_was_found(api):
    client = api(pores='echo "[]" > "$(dirname "$1")/membrane.json"', run=NO_PORE)
    id = loaded(client)
    submit(client, id)
    assert wait(client, id, 1)["state"] == "failed"
    assert client.get(f"/sessions/{id}/jobs/1/results/membrane").json() == []  # the membrane was placed
    assert client.get(f"/sessions/{id}/jobs/1/results/data").status_code == 404
