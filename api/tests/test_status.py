"""The status file of a session (storage): runs interrupted by a restart, deleted sessions, concurrent writes"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mole_api import storage
from mole_api.main import create_app
from mole_api.storage import INTERRUPTED, STATUS_FILE, JobState, SessionState, StoredSession

ANOTHER_API = "4e6736210ac44d7f86e415d1aa6f5362"  # an API process before a restart


def written(working_directory: Path, state: SessionState = SessionState.ready, job: JobState | None = None,
            instance: str = ANOTHER_API) -> str:
    """A session of a PDB entry as an API process wrote it, with one job of tunnels in this state; returns its id"""
    session = StoredSession(id="AarLX5Ovv0iuJag9oce6xA", state=state, pdb_id="1tqn", assembly_id="1", opm=True,
                            created=datetime(2026, 10, 17, 9, 33, tzinfo=UTC), mole_version="2.5.26.10.17",
                            structure_file="1tqn.cif", api_instance=instance)
    if job is not None:
        session.jobs.append(storage.Job(number=1, type="tunnels", state=job, parameters={}, mole_version="2.5.26.10.17",
                                        submitted=datetime(2026, 10, 17, 9, 34, tzinfo=UTC)))
    folder = working_directory / session.id
    folder.mkdir(parents=True)
    (folder / STATUS_FILE).write_text(session.model_dump_json())
    return session.id


def saved(working_directory: Path, id: str) -> dict:
    return json.loads((working_directory / id / STATUS_FILE).read_text())


@pytest.mark.parametrize("state", [SessionState.queued, SessionState.loading])
def test_load_interrupted_by_a_restart(client, settings, state):
    id = written(settings.working_directory, state)
    session = client.get(f"/sessions/{id}").json()  # reading it marks it
    assert (session["state"], session["error"]) == ("failed", INTERRUPTED)
    stored = saved(settings.working_directory, id)
    assert stored["state"] == "failed" and stored["loaded"].endswith("Z")
    assert stored["api_instance"] == storage.API_INSTANCE
    assert (stored["pdb_id"], stored["assembly_id"], stored["opm"]) == ("1tqn", "1", True)  # the rest is kept


@pytest.mark.parametrize("state", [JobState.queued, JobState.running])
def test_job_interrupted_by_a_restart(client, settings, state):
    id = written(settings.working_directory, job=state)
    job = client.get(f"/sessions/{id}/jobs/1").json()
    assert (job["state"], job["error"]) == ("failed", INTERRUPTED)
    assert job["finished"].endswith("Z") and job["submitted"] == "2026-10-17T09:34:00Z"
    assert client.get(f"/sessions/{id}").json()["state"] == "ready"  # the structure is still loaded


def test_run_of_this_api_process_continues(client, settings):
    id = written(settings.working_directory, job=JobState.running, instance=storage.API_INSTANCE)
    assert client.get(f"/sessions/{id}/jobs/1").json()["state"] == "running"


def test_marking_an_interrupted_run_keeps_a_new_job(settings, monkeypatch):
    """A request reads the interrupted run; before it marks it, another request marks it and starts a new job (status
    requests run in threads)"""
    id = written(settings.working_directory, job=JobState.running)
    read = storage._read

    def meanwhile(path: Path, working_directory: Path) -> StoredSession:
        stale = read(path, working_directory)
        monkeypatch.setattr(storage, "_read", read)  # once
        other = storage.load(settings.working_directory, id)  # marks it
        other.add_job("tunnels", {}, "2.5.1")
        return stale
    monkeypatch.setattr(storage, "_read", meanwhile)
    loaded = storage.load(settings.working_directory, id)
    assert [j.state for j in loaded.jobs] == [JobState.failed, JobState.queued]
    assert [j["state"] for j in saved(settings.working_directory, id)["jobs"]] == ["failed", "queued"]


@pytest.mark.parametrize("id", ["NoSuchSession", "..x", "a.b"])
def test_unknown_session(client, id):
    response = client.get(f"/sessions/{id}")
    assert response.status_code == 404
    assert response.json() == {"ComputationId": id, "SubmitId": 0, "Status": "Error", "ErrorMsg": f"Session [{id}] does not exist."}


def test_corrupt_status_file_is_500_with_a_report(settings):
    id = written(settings.working_directory)
    (settings.working_directory / id / STATUS_FILE).write_text("{x")
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.get(f"/sessions/{id}")
    assert response.status_code == 500
    assert response.json()["Status"] == "Error"


def test_a_deleted_session_is_never_written_over(settings):
    id = written(settings.working_directory, job=JobState.running, instance=storage.API_INSTANCE)
    stale = storage.load(settings.working_directory, id)  # e.g. a run's copy
    storage.load(settings.working_directory, id).delete()
    stale.update(1, JobState.failed, "late")
    assert saved(settings.working_directory, id)["deleted"]
    assert saved(settings.working_directory, id)["jobs"][0]["state"] == "running"
    assert not list((settings.working_directory / id).glob("*.tmp"))  # no temporary files left


def test_status_file_is_readable_by_others(client, settings):
    id = written(settings.working_directory, job=JobState.running)
    client.get(f"/sessions/{id}")  # rewritten: interrupted by a restart
    assert (settings.working_directory / id / STATUS_FILE).stat().st_mode & 0o777 == 0o644
