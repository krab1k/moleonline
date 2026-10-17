import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mole_api import computation
from mole_api.computation import STATUS_FILE, Computation, Status, Unit
from mole_api.main import create_app


def written_by_csharp(working_directory: Path, last: str = "Finished", instance: str = "4e6736210ac44d7f86e415d1aa6f5362") -> str:
    """A status file as the C# API wrote it (7 decimal places of seconds); returns its computation id"""
    folder = working_directory / "AarLX5Ovv0iuJag9oce6xA"
    folder.mkdir(parents=True)
    (folder / STATUS_FILE).write_text(f"""{{
  "ComputationId": "AarLX5Ovv0iuJag9oce6xA",
  "BaseDir": "{working_directory}",
  "UserStructure": false,
  "PdbId": "1tqn",
  "AssemblyId": "1",
  "DbModePores": true,
  "StructureFile": "1tqn.cif",
  "ComputationUnits": [
    {{ "SubmitId": 1, "Status": "Finished", "ErrorMsg": "", "Submitted": "2026-10-17T09:33:00.7172355Z",
       "Started": "2026-10-17T09:33:00.7302809Z", "Finished": "2026-10-17T09:33:02.1494975Z", "MoleVersion": "2.5.26.10.15" }},
    {{ "SubmitId": 2, "Status": "{last}", "ErrorMsg": "Chain Z is not in the structure.\\n", "Submitted": null,
       "Started": null, "Finished": null, "MoleVersion": null }}
  ],
  "Created": "2026-10-17T09:32:58.1Z",
  "Loaded": null,
  "ApiInstance": "{instance}"
}}""")
    return folder.name


def test_status_of_a_submission(client, settings):
    id = written_by_csharp(settings.working_directory)
    first = {"ComputationId": id, "SubmitId": 1, "Status": "Finished", "ErrorMsg": ""}
    assert client.get(f"/Status/{id}").json() == first  # the load, as before
    assert client.get(f"/Status/{id}?submitId=1").json() == first
    last = client.get(f"/Status/{id}?submitId=-1")
    assert last.status_code == 200
    assert last.json() == {"ComputationId": id, "SubmitId": 2, "Status": "Finished", "ErrorMsg": "Chain Z is not in the structure.\n"}
    missing = client.get(f"/Status/{id}?submitId=99")
    assert missing.status_code == 404
    assert missing.json() == {"ComputationId": id, "SubmitId": 99, "Status": "Error", "ErrorMsg": "SubmitId 99 not found."}


@pytest.mark.parametrize("id", ["NoSuchComputation", "..x", "a.b"])
def test_unknown_computation(client, id):
    response = client.get(f"/Status/{id}")
    assert response.status_code == 404
    assert response.json() == {"ComputationId": id, "SubmitId": 0, "Status": "Error", "ErrorMsg": f"ComputationId [{id}] does not exists.\n"}


def test_deleted_computation(client, settings):
    id = written_by_csharp(settings.working_directory, last="Deleted")
    response = client.get(f"/Status/{id}")
    assert response.status_code == 410
    assert response.json()["ErrorMsg"] == "The computation has been deleted."


@pytest.mark.parametrize("running, failed, ended", [("Running", "Error", "Finished"), ("Initializing", "FailedInitialization", "Loaded")])
def test_run_interrupted_by_a_restart(client, settings, running, failed, ended):
    id = written_by_csharp(settings.working_directory, last=running)  # by another API process
    report = client.get(f"/Status/{id}?submitId=-1").json()
    assert report["Status"] == failed
    assert report["ErrorMsg"] == "The computation was interrupted by a restart of the server. Please, run it again."
    saved = json.loads((settings.working_directory / id / STATUS_FILE).read_text())
    assert saved["ComputationUnits"][-1]["Status"] == failed
    assert (saved[ended] if ended == "Loaded" else saved["ComputationUnits"][-1][ended]).endswith("Z")
    assert saved["ApiInstance"] == computation.API_INSTANCE
    assert saved["PdbId"] == "1tqn" and saved["AssemblyId"] == "1" and saved["DbModePores"]  # the rest is kept
    assert saved["ComputationUnits"][0]["Submitted"] == "2026-10-17T09:33:00.717235Z"


def test_run_of_this_api_process_continues(client, settings):
    id = written_by_csharp(settings.working_directory, last="Running", instance=computation.API_INSTANCE)
    assert client.get(f"/Status/{id}?submitId=-1").json()["Status"] == "Running"


def test_corrupt_status_file_is_500_with_a_report(settings):
    id = written_by_csharp(settings.working_directory)
    (settings.working_directory / id / STATUS_FILE).write_text("{x")
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.get(f"/Status/{id}")
    assert response.status_code == 500
    assert response.json()["Status"] == "Error"


def test_a_deleted_computation_is_never_written_over(settings):
    id = written_by_csharp(settings.working_directory)
    stale = computation.load(settings.working_directory, id)  # e.g. a run's copy
    deleted = computation.load(settings.working_directory, id)
    deleted.ComputationUnits.append(Unit(SubmitId=3, Status=Status.Deleted))
    deleted.save()
    stale.change_status(Status.Error, "late")
    assert computation.load(settings.working_directory, id).is_deleted()
    assert not list((settings.working_directory / id).glob("*.tmp"))  # no temporary files left
