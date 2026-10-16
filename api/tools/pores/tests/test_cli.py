"""The command line: parameters as the MOLE API writes them into pores.json."""

import json
import shutil
from pathlib import Path

import pytest

from pores.cli import PoresError, run

REFERENCE = Path(__file__).parent.parent / "reference"


def parameters(tmp_path: Path, chains: list[str]) -> Path:
    computation = tmp_path / "computation"
    (computation / "1").mkdir(parents=True)
    shutil.copy(REFERENCE / "1k4c" / "data_molecule", computation / "1k4c.cif")
    path = computation / "1" / "pores.json"
    path.write_text(json.dumps({"Structure": str(computation / "1k4c.cif"), "PdbId": "1k4c",
                                "WorkingDirectory": str(computation / "1"), "Membrane": str(computation / "membrane.json"),
                                "InMembrane": True, "IsBetaBarel": False, "Chains": chains, "ProbeRadius": 13.0, "InteriorThreshold": 0.8}))
    return path


@pytest.mark.parametrize("chains, message", [
    (["X"], r"Chain X is not in the structure\. Available chains: A, B, C, A_2,"),
    (["C", "D", "Q"], r"Chains D, Q are not in the structure\."),
])
def test_unknown_chains(tmp_path, chains, message):
    """Checked before anything is downloaded or computed."""
    with pytest.raises(PoresError, match=message):
        run(parameters(tmp_path, chains))


def test_writes_membrane_and_mole_input(tmp_path, monkeypatch):
    cache = tmp_path / "opm"
    cache.mkdir()
    shutil.copy(REFERENCE / "1k4c" / "opm_1k4c.pdb", cache / "1k4c.pdb")  # fresh in the cache, nothing is downloaded
    monkeypatch.setenv("PORES_OPM_CACHE", str(cache))
    run(parameters(tmp_path, ["C", "C_2"]))

    computation = tmp_path / "computation"
    assert json.loads((computation / "membrane.json").read_text())
    mole_input = (computation / "1" / "input.xml").read_text()
    assert str(computation / "1k4c.cif") in mole_input and "C,C_2" in mole_input.replace(" ", "")
