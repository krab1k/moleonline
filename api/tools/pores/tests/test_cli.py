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
    path.write_text(json.dumps({"InMembrane": True, "IsBetaBarel": False, "InteriorThreshold": 0.8, "ProbeRadius": 13.0,
                                "UserStructure": None, "PdbId": "1k4c", "WorkingDirectory": str(computation / "1"),
                                "Chains": chains, "MemEmbedLocation": None}))
    return path


@pytest.mark.parametrize("chains, message", [
    (["X"], r"Chain X is not in the structure\. Available chains: A, B, C, A_2,"),
    (["C", " D", "Q"], r"Chains D, Q are not in the structure\."),
])
def test_unknown_chains(tmp_path, chains, message):
    """Checked before anything is downloaded or computed."""
    with pytest.raises(PoresError, match=message):
        run(parameters(tmp_path, chains))
