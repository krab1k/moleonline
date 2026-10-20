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


def tunnel_job(working_directory: Path, id: str, number: int) -> None:
    """The unit becomes a job of tunnels: its folder has MOLE's parameters"""
    (working_directory / id / str(number)).mkdir(parents=True)
    (working_directory / id / str(number) / "mole.json").write_text("{}")


def test_jobs_of_the_csharp_api(client, settings):
    id = written_by_csharp(settings.working_directory)
    tunnel_job(settings.working_directory, id, 1)
    tunnel_job(settings.working_directory, id, 2)
    jobs = client.get(f"/sessions/{id}").json()["jobs"]
    assert [(j["number"], j["state"], j["error"]) for j in jobs] == [(1, "finished", ""), (2, "finished", "Chain Z is not in the structure.\n")]
    assert jobs[0]["submitted"] == "2026-10-17T09:33:00.717235Z"
    missing = client.get(f"/sessions/{id}/jobs/99")
    assert missing.status_code == 404
    assert missing.json()["ErrorMsg"] == "Job 99 does not exist."


@pytest.mark.parametrize("id", ["NoSuchComputation", "..x", "a.b"])
def test_unknown_session(client, id):
    response = client.get(f"/sessions/{id}")
    assert response.status_code == 404
    assert response.json() == {"ComputationId": id, "SubmitId": 0, "Status": "Error", "ErrorMsg": f"ComputationId [{id}] does not exists.\n"}


def test_deleted_session(client, settings):
    id = written_by_csharp(settings.working_directory, last="Deleted")
    for response in (client.get(f"/sessions/{id}"), client.get(f"/sessions/{id}/jobs/1")):
        assert response.status_code == 410
        assert response.json()["ErrorMsg"] == "The computation has been deleted."


@pytest.mark.parametrize("running, failed, ended", [("Running", "Error", "Finished"), ("Initializing", "FailedInitialization", "Loaded")])
def test_run_interrupted_by_a_restart(client, settings, running, failed, ended):
    id = written_by_csharp(settings.working_directory, last=running)  # by another API process
    tunnel_job(settings.working_directory, id, 2)
    job = client.get(f"/sessions/{id}/jobs/2").json()  # reading it marks it
    assert job["state"] == "failed"
    assert job["error"] == "The computation was interrupted by a restart of the server. Please, run it again."
    saved = json.loads((settings.working_directory / id / STATUS_FILE).read_text())
    assert saved["ComputationUnits"][-1]["Status"] == failed
    assert (saved[ended] if ended == "Loaded" else saved["ComputationUnits"][-1][ended]).endswith("Z")
    assert saved["ApiInstance"] == computation.API_INSTANCE
    assert saved["PdbId"] == "1tqn" and saved["AssemblyId"] == "1" and saved["DbModePores"]  # the rest is kept
    assert saved["ComputationUnits"][0]["Submitted"] == "2026-10-17T09:33:00.717235Z"


def test_marking_an_interrupted_run_keeps_a_new_submission(settings, monkeypatch):
    """A request reads the interrupted run; before it marks it, another request marks it and starts a new submission
    (status requests run in threads)"""
    id = written_by_csharp(settings.working_directory, last="Running")
    path = settings.working_directory / id / STATUS_FILE
    read = computation._read

    def meanwhile(path: Path) -> Computation:
        stale = read(path)
        monkeypatch.setattr(computation, "_read", read)  # once
        other = computation.load(settings.working_directory, id)  # marks it
        other.add_submission("2.5.1")
        other.change_status(Status.Running)
        return stale
    monkeypatch.setattr(computation, "_read", meanwhile)
    loaded = computation.load(settings.working_directory, id)
    assert [u.Status for u in loaded.ComputationUnits] == [Status.Finished, Status.Error, Status.Running]
    assert [u["Status"] for u in json.loads(path.read_text())["ComputationUnits"]] == ["Finished", "Error", "Running"]


def test_run_of_this_api_process_continues(client, settings):
    id = written_by_csharp(settings.working_directory, last="Running", instance=computation.API_INSTANCE)
    tunnel_job(settings.working_directory, id, 2)
    job = client.get(f"/sessions/{id}/jobs/2").json()
    assert job["state"] == "queued" and "interrupted" not in job["error"]  # not marked; queued: it has no Started time


def test_corrupt_status_file_is_500_with_a_report(settings):
    id = written_by_csharp(settings.working_directory)
    (settings.working_directory / id / STATUS_FILE).write_text("{x")
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.get(f"/sessions/{id}")
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


def test_status_file_is_readable_by_others(client, settings):
    id = written_by_csharp(settings.working_directory, last="Running")
    client.get(f"/sessions/{id}")  # rewritten: interrupted by a restart
    assert (settings.working_directory / id / STATUS_FILE).stat().st_mode & 0o777 == 0o644


def test_session_of_a_csharp_computation_is_ready(client, settings):
    """Status files of the C# API have no Loaded time: the load's state comes from its status"""
    id = written_by_csharp(settings.working_directory)
    session = client.get(f"/sessions/{id}").json()
    assert (session["state"], session["error"], session["loaded"]) == ("ready", "", None)
    results = settings.working_directory / id / "0" / "json" / "data.json"  # what the load found
    results.parent.mkdir(parents=True)
    results.write_text('{"Channels": {}}')
    response = client.get(f"/sessions/{id}/data")
    assert response.status_code == 200 and response.json() == {"Channels": {}}


def test_session_of_a_csharp_computation_that_failed_to_load(client, settings):
    id = written_by_csharp(settings.working_directory)
    path = settings.working_directory / id / STATUS_FILE
    saved = json.loads(path.read_text())
    saved["ComputationUnits"] = [{**saved["ComputationUnits"][0], "Status": "FailedInitialization", "ErrorMsg": "No atoms.\n"}]
    path.write_text(json.dumps(saved))
    session = client.get(f"/sessions/{id}").json()
    assert (session["state"], session["error"]) == ("failed", "No atoms.\n")
