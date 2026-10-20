"""Files of a session for download: its structure, what its load found, its jobs' results, written here by a fake MOLE"""

import io
import zipfile

import pytest

from .conftest import STRUCTURE, upload, wait, wait_session, wait_for_file
from .test_runner import UNTIL_GO
from .test_submit import submit

# MOLE's results next to its input: data.json, the viewers' scripts, the tunnels' profiles
RESULTS = """d="$(dirname "$1")"; mkdir -p "$d/json" "$d/pymol" "$d/chimera" "$d/vmd" "$d/pdb/profile/more"
echo '{"Channels": {}}' > "$d/json/data.json"; echo pymol > "$d/pymol/complex.py"; echo chimera > "$d/chimera/complex.py"
echo vmd > "$d/vmd/complex"; echo a > "$d/pdb/profile/a.pdb"; echo b > "$d/pdb/profile/more/b.pdb\""""


@pytest.fixture
def finished(api):
    """A client and a session with one finished job"""
    client = api(load=RESULTS, run=RESULTS)
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    wait_session(client, id)
    submit(client, id)
    assert wait(client, id, 1)["state"] == "finished"
    return client, id


@pytest.mark.parametrize("format, name, media_type, content", [
    ("data", "mole_channels_{id}_1.json", "application/json", b'{"Channels": {}}\n'),
    ("pymol", "mole_channels_{id}_1_pymol.py", "text/plain", b"pymol\n"),
    ("chimera", "mole_channels_{id}_1_chimera.py", "text/plain", b"chimera\n"),
    ("vmd", "mole_channels_{id}_1.tk", "text/plain", b"vmd\n"),
], ids=["data", "pymol", "chimera", "vmd"])  # not the contents: the test id is in the environment of MOLE
def test_job_results(finished, format, name, media_type, content):
    client, id = finished
    response = client.get(f"/sessions/{id}/jobs/1/results/{format}")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="{name.format(id=id)}"'
    assert response.headers["content-type"].startswith(media_type)
    assert response.content == content


def test_zips(finished):
    client, id = finished
    report = client.get(f"/sessions/{id}/jobs/1/results/report")
    assert report.headers["content-disposition"] == f'attachment; filename="mole_channels_{id}_1.zip"'
    assert "1tqn.cif" in zipfile.ZipFile(io.BytesIO(report.content)).namelist()
    profiles = client.get(f"/sessions/{id}/jobs/1/results/pdb")
    assert profiles.headers["content-disposition"] == f'attachment; filename="mole_channels_{id}_1_pdb.zip"'
    assert sorted(zipfile.ZipFile(io.BytesIO(profiles.content)).namelist()) == ["a.pdb", "more/b.pdb"]


def test_structure_and_what_the_load_found(finished):
    client, id = finished
    structure = client.get(f"/sessions/{id}/structure")
    assert structure.status_code == 200 and structure.content == STRUCTURE
    assert structure.headers["content-disposition"] == 'attachment; filename="1tqn.cif"'
    assert client.get(f"/sessions/{id}/data").json() == {"Channels": {}}


@pytest.mark.parametrize("path, status, message", [
    ("jobs/1/results/xyz", 400, "Input format error. path.format: Input should be 'data', 'report', 'pymol', 'chimera', 'vmd', 'pdb' or 'membrane'"),
    ("jobs/99/results/data", 404, "Job 99 does not exist."),
    ("jobs/1/results/membrane", 404, "The job has no membrane results."),  # not a pore job
])
def test_refused(finished, path, status, message):
    client, id = finished
    response = client.get(f"/sessions/{id}/{path}")
    assert response.status_code == status
    assert response.json()["ErrorMsg"] == message


@pytest.mark.parametrize("run", ["exit 1", RESULTS + "; echo 'MOLE failed' >&2"])  # also when MOLE wrote some
def test_no_results_of_a_failed_job(api, run):
    client = api(run=run)
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    wait_session(client, id)
    submit(client, id)
    wait(client, id, 1)
    for format in ("data", "report", "pdb", "pymol"):
        response = client.get(f"/sessions/{id}/jobs/1/results/{format}")
        assert response.status_code == 404
        assert response.json()["ErrorMsg"] == "Job 1 has no results, it is failed."
    assert client.get(f"/sessions/{id}/structure").status_code == 200


def test_no_results_while_running(api):
    client = api(run=RESULTS + "; " + UNTIL_GO)
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    wait_session(client, id)
    submit(client, id)
    folder = client.app.state.settings.working_directory / id / "jobs" / "1"
    wait_for_file(folder / "started")
    assert (folder / "json" / "data.json").exists()  # MOLE writes as it goes
    assert client.get(f"/sessions/{id}/jobs/1/results/data").status_code == 404
    (folder / "go").touch()
    assert wait(client, id, 1)["state"] == "finished"
    assert client.get(f"/sessions/{id}/jobs/1/results/data").status_code == 200
    assert sorted(p.name for p in folder.glob("report.zip*")) == ["report.zip"]  # written under another name first


def test_no_data_of_a_failed_load(api):
    client = api(load="exit 1")
    id = upload(client, "1tqn.cif", STRUCTURE).json()["id"]
    assert wait_session(client, id)["state"] == "failed"
    response = client.get(f"/sessions/{id}/data")
    assert response.status_code == 404
    assert response.json()["ErrorMsg"] == "The session has no data, its structure has not been loaded."


def test_unknown_and_deleted_session(finished):
    client, id = finished
    assert client.get("/sessions/nope/structure").status_code == 404
    client.delete(f"/sessions/{id}")
    for path in ("structure", "data", "jobs/1/results/data", "active-sites"):
        assert client.get(f"/sessions/{id}/{path}").status_code == 410


def test_compressed_for_clients_that_accept_it(finished):
    client, id = finished
    structure = f"/sessions/{id}/structure"
    assert client.get(structure, headers={"Accept-Encoding": "gzip"}).headers["content-encoding"] == "gzip"
    assert "content-encoding" not in client.get(structure, headers={"Accept-Encoding": "identity"}).headers
    report = f"/sessions/{id}/jobs/1/results/report"
    assert "content-encoding" not in client.get(report, headers={"Accept-Encoding": "gzip"}).headers  # a zip
