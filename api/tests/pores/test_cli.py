"""The command line: parameters as the MOLE API writes them into pores.json."""

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from mole_api.pores import cli
from mole_api.pores.cli import PoresError, main, run
from mole_api.pores.membrane import MembraneError
from mole_api.pores.path import PathError

REFERENCE = Path(__file__).parent / "data"


def parameters(tmp_path: Path, chains: list[str], **changed) -> Path:
    computation = tmp_path / "computation"
    (computation / "1").mkdir(parents=True)
    shutil.copy(REFERENCE / "1k4c" / "data_molecule", computation / "1k4c.cif")
    path = computation / "1" / "pores.json"
    path.write_text(json.dumps({"Structure": str(computation / "1k4c.cif"), "PdbId": "1k4c",
                                "WorkingDirectory": str(computation / "1"), "Membrane": str(computation / "membrane.json"),
                                "OpmCache": str(tmp_path / "opm"), "InMembrane": True, "IsBetaBarrel": False, "Chains": chains, "ProbeRadius": 13.0, "InteriorThreshold": 0.8}
                               | changed))
    return path


@pytest.mark.parametrize("chains, message", [
    (["X"], r"Chain X is not in the structure\. Available chains: A, B, C, A_2,"),
    (["C", "D", "Q"], r"Chains D, Q are not in the structure\."),
])
def test_unknown_chains(tmp_path, chains, message):
    """Checked before anything is downloaded or computed."""
    with pytest.raises(PoresError, match=message):
        run(parameters(tmp_path, chains))


def opm_in_cache(tmp_path) -> None:
    """The cache of parameters()"""
    cache = tmp_path / "opm"
    cache.mkdir()
    shutil.copy(REFERENCE / "1k4c" / "opm_1k4c.pdb", cache / "1k4c.pdb")  # fresh in the cache, nothing is downloaded


def test_writes_membrane_and_mole_input(tmp_path):
    opm_in_cache(tmp_path)
    run(parameters(tmp_path, ["C", "C_2"]))

    computation = tmp_path / "computation"
    assert json.loads((computation / "membrane.json").read_text())
    mole_input = (computation / "1" / "input.xml").read_text()
    assert str(computation / "1k4c.cif") in mole_input and "C,C_2" in mole_input.replace(" ", "")


def test_unexpected_error_is_one_line(tmp_path, monkeypatch, capsys):
    """The API shows stderr to the user: a structure without a model (IndexError) is reported, not traced back."""
    path = parameters(tmp_path, [])
    (tmp_path / "computation" / "1k4c.cif").write_text("data_x\n_atom_site.id 1\n")
    monkeypatch.setattr("sys.argv", ["pores", str(path)])
    with pytest.raises(SystemExit) as exit:
        main()
    assert exit.value.code == 1
    assert capsys.readouterr().err == "The pore computation failed unexpectedly (IndexError).\n"


def test_invalid_membrane_is_not_written(tmp_path, monkeypatch):
    """numpy only warns and computes on with NaN: neither the web nor MOLE gets it."""
    opm_in_cache(tmp_path)
    computed = cli.membrane_from_opm

    def with_nan(*arguments):
        membrane = computed(*arguments)
        membrane.translation = np.full(3, np.nan)
        return membrane
    monkeypatch.setattr(cli, "membrane_from_opm", with_nan)
    with pytest.raises(MembraneError, match="invalid numbers"):
        run(parameters(tmp_path, []))
    assert not (tmp_path / "computation" / "membrane.json").exists()


def test_invalid_pore_ends_are_not_written(tmp_path, monkeypatch):
    opm_in_cache(tmp_path)
    monkeypatch.setattr(cli, "end_points", lambda *arguments: (np.zeros(3), np.array([0.0, np.nan, 1.0])))
    with pytest.raises(PathError, match="invalid numbers"):
        run(parameters(tmp_path, []))
    assert not (tmp_path / "computation" / "1" / "input.xml").exists()


def test_memembed_of_the_parameters(tmp_path, monkeypatch):
    """Without an OPM entry (PdbId null) MEMEMBED predicts the membrane, the one the API configured"""
    called = []

    def memembed(structure, executable, beta_barrel, chains):
        called.append((executable, beta_barrel, chains))
        raise MembraneError("stop")
    monkeypatch.setattr(cli, "membrane_from_memembed", memembed)
    with pytest.raises(MembraneError, match="stop"):
        run(parameters(tmp_path, ["A"], PdbId=None, Memembed="/opt/memembed/memembed", IsBetaBarrel=True))
    assert called == [("/opt/memembed/memembed", True, ["A"])]


def test_memembed_not_configured(tmp_path):
    with pytest.raises(PoresError, match="MEMEMBED is not configured"):
        run(parameters(tmp_path, [], PdbId=None))
