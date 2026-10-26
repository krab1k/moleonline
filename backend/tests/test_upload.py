"""POST /sessions/upload: one PDB or mmCIF structure, optionally gzipped, at most 30 MB also when unpacked"""

import gzip
from xml.etree import ElementTree as ET

import pytest

from .conftest import MOLE_VERSION, STRUCTURE, query, upload, wait_session

MB = 1024 * 1024


def test_upload_is_loaded(client, settings):
    response = upload(client, "1tqn.cif", STRUCTURE)
    assert response.status_code == 202
    session = response.json()
    id = session["id"]
    assert response.headers["location"] == f"/sessions/{id}"
    assert session["state"] == "queued" and session["error"] == ""
    assert session["pdb_id"] is None and session["assembly_id"] is None and session["loaded"] is None
    loaded = wait_session(client, id)
    assert loaded["state"] == "ready" and loaded["error"] == ""
    assert loaded["created"] == session["created"] <= loaded["loaded"]

    folder = settings.working_directory / id
    assert (folder / "1tqn.cif").read_bytes() == STRUCTURE
    scratch = settings.scratch_directory / id
    xml = ET.parse(scratch / "load.xml").getroot()  # as the fake MOLE kept it
    assert xml.findtext("Input") == str(folder / "1tqn.cif")
    assert xml.findtext("WorkingDirectory") == str(scratch / "load")
    assert not (scratch / "load").exists()  # removed after the run
    assert xml.find("Params/Cavity").attrib == {"ProbeRadius": "5", "InteriorThreshold": "1.1", "IgnoreHETAtoms": "0",
                                                "IgnoreHydrogens": "0"}

    [saved] = query(f"SELECT pdb_id, structure_file, state, mole_version FROM sessions WHERE id = '{id}'")
    assert tuple(saved) == (None, "1tqn.cif", "ready", MOLE_VERSION)  # the version read from MOLE when the API started


@pytest.mark.parametrize("content", [gzip.compress(STRUCTURE), gzip.compress(STRUCTURE[:1000]) + gzip.compress(STRUCTURE[1000:])],
                         ids=["one member", "two members"])
def test_gzipped_upload_is_stored_unpacked(client, settings, content):  # also of several gzip members
    id = upload(client, "1tqn.cif.gz", content).json()["id"]
    assert wait_session(client, id)["state"] == "ready"
    assert [p.name for p in (settings.working_directory / id).glob("*.cif*")] == ["1tqn.cif"]
    assert (settings.working_directory / id / "1tqn.cif").read_bytes() == STRUCTURE


def test_exactly_30_mb_is_accepted(client):
    response = upload(client, "large.pdb", bytes(30 * MB))
    assert response.status_code == 202
    wait_session(client, response.json()["id"])


def test_name_cannot_leave_the_folder(client, settings):
    id = upload(client, "../../x.pdb", STRUCTURE).json()["id"]
    assert (settings.working_directory / id / "x.pdb").exists()


@pytest.mark.parametrize("name, stored", [
    ("my protein (1).PDB", "my_protein__1_.pdb"),
    ("1tqn.model-1.cif.GZ", "1tqn.model-1.cif"),
    ("蛋白.cif", "structure.cif"),  # nothing of the name is left
    ("..cif", "structure.cif"),
    ("\"quoted\\name\".cif", "_22quoted_name_22.cif"),  # clients send quotes as %22, as browsers do
])
def test_name_is_stored_with_safe_characters(client, settings, name, stored):
    """Other characters broke the structure's download (Content-Disposition) and would go into MOLE's viewer scripts."""
    id = upload(client, name, gzip.compress(STRUCTURE) if name.endswith("GZ") else STRUCTURE).json()["id"]
    assert wait_session(client, id)["state"] == "ready"
    assert query(f"SELECT structure_file FROM sessions WHERE id = '{id}'") == [(stored,)]
    response = client.get(f"/sessions/{id}/structure")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="{stored}"'
    assert response.content == STRUCTURE  # unpacked, also under the shortened name


GZIP = gzip.compress(STRUCTURE)


@pytest.mark.parametrize("name, content, expected, message", [
    ("empty.pdb", b"", 422, "No structure file"),
    ("1tqn.pdb1", STRUCTURE, 422, "Unsupported file type"),
    ("1tqn.txt", STRUCTURE, 422, "Unsupported file type"),
    ("large.pdb", bytes(30 * MB + 1), 413, "at most 30 MB can be uploaded"),
    ("larger.pdb", bytes(32 * MB), 413, "at most 30 MB can be uploaded"),  # a request over 31 MB
    ("large.pdb.gz", gzip.compress(bytes(30 * MB + 1)), 413, "unpacks to more than 30 MB"),
    ("broken.cif.gz", b"xxxxxxxx", 422, "not a valid gzip file"),
    ("one_byte.cif.gz", b"x", 422, "not a valid gzip file"),
    ("header_only.cif.gz", GZIP[:5], 422, "not a valid gzip file"),
    ("truncated.cif.gz", GZIP[:len(GZIP) // 2], 422, "it is incomplete"),
    ("corrupt.cif.gz", GZIP[:-8] + bytes(8), 422, "not a valid gzip file"),  # the checksum and size do not match
], ids=lambda value: value if isinstance(value, str) and len(value) < 30 else "")
def test_refused_upload_creates_no_computation(client, settings, name, content, expected, message):
    response = upload(client, name, content)
    assert response.status_code == expected
    assert response.json()["status"] == expected
    assert message in response.json()["detail"]
    assert list(settings.working_directory.iterdir()) == []  # no computation, no temporary file
    assert query("SELECT count(*) FROM sessions") == [(0,)]


def test_upload_without_a_file_is_refused(client):
    assert client.post("/sessions/upload", data={"other": "x"}, files={"x": ("a.pdb", b"x")}).status_code == 422
