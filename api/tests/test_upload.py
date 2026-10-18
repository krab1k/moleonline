"""POST /Init with a file: one PDB or mmCIF structure, optionally gzipped, at most 30 MB also when unpacked"""

import gzip
import json
from xml.etree import ElementTree as ET

import pytest

from .conftest import MOLE_VERSION, STRUCTURE, upload, wait

MB = 1024 * 1024


def test_upload_is_loaded(client, settings):
    response = upload(client, "1tqn.cif", STRUCTURE)
    assert response.status_code == 200
    id = response.json()["ComputationId"]
    assert response.json() == {"ComputationId": id, "SubmitId": 1, "Status": "Initializing", "ErrorMsg": ""}
    assert wait(client, id) == {"ComputationId": id, "SubmitId": 1, "Status": "Initialized", "ErrorMsg": ""}

    folder = settings.working_directory / id
    assert (folder / "1tqn.cif").read_bytes() == STRUCTURE
    xml = ET.parse(folder / "0" / "input.xml").getroot()
    assert xml.findtext("Input") == str(folder / "1tqn.cif")
    assert xml.findtext("WorkingDirectory") == str(folder / "0")
    assert xml.find("Params/Cavity").attrib == {"ProbeRadius": "5", "InteriorThreshold": "1.1", "IgnoreHETAtoms": "0", "IgnoreHydrogens": "0"}

    saved = json.loads((folder / "_object.json").read_text())
    assert saved["UserStructure"] and saved["PdbId"] == "" and saved["StructureFile"] == "1tqn.cif"
    unit = saved["ComputationUnits"][0]
    assert saved["Created"] == unit["Submitted"] <= unit["Started"] <= saved["Loaded"]
    assert saved["Created"].endswith("Z")
    assert unit["MoleVersion"] == MOLE_VERSION  # read from MOLE when the API started


@pytest.mark.parametrize("content", [gzip.compress(STRUCTURE), gzip.compress(STRUCTURE[:1000]) + gzip.compress(STRUCTURE[1000:])],
                         ids=["one member", "two members"])
def test_gzipped_upload_is_stored_unpacked(client, settings, content):  # also of several gzip members
    id = upload(client, "1tqn.cif.gz", content).json()["ComputationId"]
    assert wait(client, id)["Status"] == "Initialized"
    assert [p.name for p in (settings.working_directory / id).glob("*.cif*")] == ["1tqn.cif"]
    assert (settings.working_directory / id / "1tqn.cif").read_bytes() == STRUCTURE


def test_exactly_30_mb_is_accepted(client):
    response = upload(client, "large.pdb", bytes(30 * MB))
    assert response.status_code == 200
    wait(client, response.json()["ComputationId"])


def test_name_cannot_leave_the_folder(client, settings):
    id = upload(client, "../../x.pdb", STRUCTURE).json()["ComputationId"]
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
    id = upload(client, name, gzip.compress(STRUCTURE) if name.endswith("GZ") else STRUCTURE).json()["ComputationId"]
    assert wait(client, id)["Status"] == "Initialized"
    assert json.loads((settings.working_directory / id / "_object.json").read_text())["StructureFile"] == stored
    response = client.get(f"/Data/{id}", params={"format": "molecule"})
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="{stored}"'
    assert response.content == STRUCTURE  # unpacked, also under the shortened name


GZIP = gzip.compress(STRUCTURE)


@pytest.mark.parametrize("name, content, expected, message", [
    ("empty.pdb", b"", 400, "No structure file"),
    ("1tqn.pdb1", STRUCTURE, 400, "Unsupported file type"),
    ("1tqn.txt", STRUCTURE, 400, "Unsupported file type"),
    ("large.pdb", bytes(30 * MB + 1), 413, "at most 30 MB can be uploaded"),
    ("larger.pdb", bytes(32 * MB), 413, "at most 30 MB can be uploaded"),  # a request over 31 MB
    ("large.pdb.gz", gzip.compress(bytes(30 * MB + 1)), 413, "unpacks to more than 30 MB"),
    ("broken.cif.gz", b"xxxxxxxx", 400, "not a valid gzip file"),
    ("one_byte.cif.gz", b"x", 400, "not a valid gzip file"),
    ("header_only.cif.gz", GZIP[:5], 400, "not a valid gzip file"),
    ("truncated.cif.gz", GZIP[:len(GZIP) // 2], 400, "it is incomplete"),
    ("corrupt.cif.gz", GZIP[:-8] + bytes(8), 400, "not a valid gzip file"),  # the checksum and size do not match
], ids=lambda value: value if isinstance(value, str) and len(value) < 30 else "")
def test_refused_upload_creates_no_computation(client, settings, name, content, expected, message):
    response = upload(client, name, content)
    assert response.status_code == expected
    assert response.json()["Status"] == "Error" and response.json()["ComputationId"] is None
    assert message in response.json()["ErrorMsg"]
    assert [p.name for p in settings.working_directory.iterdir()] == ["Mole_api_IP_log.csv"]  # no computation, no temporary file


def test_upload_without_a_file_is_refused(client):
    assert client.post("/Init", data={"other": "x"}, files={"x": ("a.pdb", b"x")}).status_code == 400
