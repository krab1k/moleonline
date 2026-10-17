"""Active sites from M-CSA (Resources/mcsa_sites.csv.gz), as /CSA serves them for PDB entries"""

import pytest

from mole_api.csa import CSA

from .conftest import CSA as CSA_FILE
from .conftest import STRUCTURE, upload, wait
from .test_pdbe import FULL, FakePDBe


@pytest.fixture(scope="module")
def csa() -> CSA:
    return CSA(CSA_FILE)


def sites(csa: CSA, pdb_id: str) -> list[str]:
    return [" ".join(f"{r.Chain}:{r.Name}{r.SequenceNumber}" for r in site) for site in csa.get(pdb_id)]


def test_one_site_per_chain(csa):
    # M-CSA groups the four copies together; the web places a site at the centre of its residues
    assert sites(csa, "13pk") == ["A:Arg39 A:Lys219 A:Gly376 A:Gly399", "B:Arg39 B:Lys219 B:Gly376 B:Gly399",
                                  "C:Arg39 C:Lys219 C:Gly376 C:Gly399", "D:Arg39 D:Lys219 D:Gly376 D:Gly399"]


def test_descriptions_of_one_site_are_merged(csa):
    # four M-CSA entries describe the trypsin site of each chain with slightly different residues
    assert len(sites(csa, "1a0j")) == 4
    assert sites(csa, "1a0j")[0] == "A:His57 A:Asp102 A:Gln192 A:Gly193 A:Asp194 A:Ser195 A:Gly196"


def test_residues_have_the_names_of_the_structure(csa):
    # M-CSA names a few residues differently in different entries; checked in the structures
    assert "C:Cys25" in sites(csa, "1nb3")[2]
    assert "Gln25" not in " ".join(sites(csa, "1nb3"))
    assert "A:Tyr280" in sites(csa, "1b8f")[0]
    assert "280" not in " ".join(sites(csa, "1gkm"))  # not in the structure


def test_entries(csa):
    assert sites(csa, "1tqn") == ["A:Thr309 A:Phe435 A:Cys442"]  # the example of the documentation
    assert sites(csa, "1a30") == ["A:Asp25 A:Thr26 A:Gly27 B:Asp25 B:Thr26 B:Gly27"]  # HIV protease: one site of both chains
    assert sites(csa, "1cqj") == ["A:Glu208 A:His246 B:Tyr109 B:Glu197", "D:Glu208 D:His246 E:Tyr109 E:Glu197"]  # a site of two different chains, per copy
    assert len(sites(csa, "1a50")) == 2  # tryptophan synthase: the sites of its two subunits, 40 A apart, stay apart
    assert sites(csa, "1crn") == []  # not an enzyme


def test_sites_of_a_computation(client):
    FakePDBe(client, {FULL: STRUCTURE})
    id = client.post("/Init/1TQN?assembly=asymmetric").json()["ComputationId"]
    assert client.get(f"/CSA/{id}").json() == [[{"Name": "Thr", "Chain": "A", "SequenceNumber": 309}, {"Name": "Phe", "Chain": "A", "SequenceNumber": 435},
                                                 {"Name": "Cys", "Chain": "A", "SequenceNumber": 442}]]
    wait(client, id)
    client.delete(f"/Delete/{id}")
    assert client.get(f"/CSA/{id}").status_code == 410


def test_none_for_uploads(client):
    id = upload(client, "1tqn.cif", STRUCTURE).json()["ComputationId"]  # M-CSA knows PDB entries only
    assert client.get(f"/CSA/{id}").json() == []
    assert client.get("/CSA/nope").status_code == 404
