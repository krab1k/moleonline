"""GET /Data: results of a submission for download, written here by a fake MOLE"""

import io
import json
import zipfile

import pytest

from .conftest import STRUCTURE, upload, wait, wait_for_file
from .test_runner import UNTIL_GO
from .test_submit import submit

# MOLE's results next to its input: data.json, the viewers' scripts, the tunnels' profiles
RESULTS = """d="$(dirname "$1")"; mkdir -p "$d/json" "$d/pymol" "$d/chimera" "$d/vmd" "$d/pdb/profile/more"
echo '{"Channels": {}}' > "$d/json/data.json"; echo pymol > "$d/pymol/complex.py"; echo chimera > "$d/chimera/complex.py"
echo vmd > "$d/vmd/complex"; echo a > "$d/pdb/profile/a.pdb"; echo b > "$d/pdb/profile/more/b.pdb\""""


@pytest.fixture
def finished(api):
    """A client and a computation with one finished submission"""
    client = api(load=RESULTS, run=RESULTS)
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    wait(client, id)
    submit(client, id)
    assert wait(client, id, 1)["Status"] == "Finished"
    return client, id


@pytest.mark.parametrize("format, name, media_type, content", [
    ("json", "mole_channels_{id}_1.json", "application/json", b'{"Channels": {}}\n'),
    ("molecule", "1tqn.cif", "text/plain", STRUCTURE),
    ("pymol", "mole_channels_{id}_1_pymol.py", "text/plain", b"pymol\n"),
    ("chimera", "mole_channels_{id}_1_chimera.py", "text/plain", b"chimera\n"),
    ("vmd", "mole_channels_{id}_1.tk", "text/plain", b"vmd\n"),
], ids=["json", "molecule", "pymol", "chimera", "vmd"])  # not the contents: the test id is in the environment of MOLE
def test_files(finished, format, name, media_type, content):
    client, id = finished
    response = client.get(f"/Data/{id}?submitId=1&format={format}")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="{name.format(id=id)}"'
    assert response.headers["content-type"].startswith(media_type)
    assert response.content == content


def test_zips(finished):
    client, id = finished
    report = client.get(f"/Data/{id}?submitId=1&format=report")
    assert report.headers["content-disposition"] == f'attachment; filename="mole_channels_{id}_1.zip"'
    assert "1tqn.cif" in zipfile.ZipFile(io.BytesIO(report.content)).namelist()
    profiles = client.get(f"/Data/{id}?submitId=1&format=pdb")
    assert profiles.headers["content-disposition"] == f'attachment; filename="mole_channels_{id}_1_pdb.zip"'
    assert sorted(zipfile.ZipFile(io.BytesIO(profiles.content)).namelist()) == ["a.pdb", "more/b.pdb"]


def test_submission_by_default_the_last_one(finished):
    client, id = finished
    assert client.get(f"/Data/{id}").headers["content-disposition"] == f'attachment; filename="mole_channels_{id}_1.json"'
    assert client.get(f"/Data/{id}?submitId=0").json() == {"Channels": {}}  # the load's


@pytest.mark.parametrize("query, status, message", [
    ("submitId=1&format=xyz", 400, "Unknown format 'xyz', the formats are json, report, molecule, pymol, chimera, vmd, pdb, membrane."),
    ("submitId=99", 404, "Submission 99 does not exist."),
    ("submitId=-2", 404, "Submission -2 does not exist."),
    ("format=membrane", 404, "Data in format 'membrane' are not available."),  # not a pore computation
])
def test_refused(finished, query, status, message):
    client, id = finished
    response = client.get(f"/Data/{id}?{query}")
    assert response.status_code == status
    assert response.json()["ErrorMsg"] == message


@pytest.mark.parametrize("run", ["exit 1", RESULTS + "; echo 'MOLE failed' >&2"])  # also when MOLE wrote some
def test_no_results_of_a_failed_submission(api, run):
    client = api(run=run)
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    wait(client, id)
    submit(client, id)
    wait(client, id, 1)
    for format in ("json", "report", "pdb", "pymol"):
        response = client.get(f"/Data/{id}?submitId=1&format={format}")
        assert response.status_code == 404
        assert response.json()["ErrorMsg"] == "Submission 1 has no results, it has not finished or it failed."
    assert client.get(f"/Data/{id}?submitId=1&format=molecule").status_code == 200


def test_no_results_while_running(api):
    client = api(run=RESULTS + "; " + UNTIL_GO)
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    wait(client, id)
    submit(client, id)
    folder = client.app.state.settings.working_directory / id / "1"
    wait_for_file(folder / "started")
    assert (folder / "json" / "data.json").exists()  # MOLE writes as it goes
    assert client.get(f"/Data/{id}?submitId=1").status_code == 404
    (folder / "go").touch()
    assert wait(client, id, 1)["Status"] == "Finished"
    assert client.get(f"/Data/{id}?submitId=1").status_code == 200
    assert sorted(p.name for p in folder.glob("report.zip*")) == ["report.zip"]  # written under another name first


def test_results_of_the_load(api):
    client = api(load=RESULTS)
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]
    assert wait(client, id)["Status"] == "Initialized"
    assert client.get(f"/Data/{id}?submitId=0").json() == {"Channels": {}}


def test_unknown_and_deleted_computation(finished):
    client, id = finished
    assert client.get("/Data/nope").status_code == 404
    client.delete(f"/Delete/{id}")
    assert client.get(f"/Data/{id}").status_code == 410


def test_compressed_for_clients_that_accept_it(finished):
    client, id = finished
    assert client.get(f"/Data/{id}?format=molecule", headers={"Accept-Encoding": "gzip"}).headers["content-encoding"] == "gzip"
    assert "content-encoding" not in client.get(f"/Data/{id}?format=molecule", headers={"Accept-Encoding": "identity"}).headers
    assert "content-encoding" not in client.get(f"/Data/{id}?format=report", headers={"Accept-Encoding": "gzip"}).headers  # a zip
