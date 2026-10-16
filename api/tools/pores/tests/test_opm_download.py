"""Downloading OPM files: missing entries, outages and the cache, without network access."""

import http.client
import io
import os
import time
import urllib.error

import pytest

from pores import opm
from pores.membrane import MembraneError

OPM_FILE = "REMARK OPM file\n"


class FakeOpm:
    """Answers of urlopen: a text, or an exception to raise."""

    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def __call__(self, url, timeout):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return io.BytesIO(self.answer.encode())


def http_error(code):
    return urllib.error.HTTPError("https://opm", code, "error", {}, None)


@pytest.fixture
def fake(monkeypatch):
    def install(answer):
        fake = FakeOpm(answer)
        monkeypatch.setattr(opm.urllib.request, "urlopen", fake)
        return fake
    return install


def test_downloaded_and_cached(fake, tmp_path):
    server = fake(OPM_FILE)
    assert opm.download_opm("1K4C", tmp_path) == OPM_FILE
    assert (tmp_path / "1k4c.pdb").read_text() == OPM_FILE
    assert opm.download_opm("1k4c", tmp_path) == OPM_FILE
    assert server.calls == 1  # the second time from the cache
    assert [p.name for p in tmp_path.iterdir()] == ["1k4c.pdb"]  # no temporary files left


@pytest.mark.parametrize("code", [403, 404])
def test_missing_entry(fake, tmp_path, code):
    fake(http_error(code))
    assert opm.download_opm("1v9e", tmp_path) is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure, message", [
    (urllib.error.URLError("Name or service not known"), "not available at the moment"),
    (TimeoutError("timed out"), "not available at the moment"),
    (ConnectionResetError("reset"), "not available at the moment"),
    (http.client.IncompleteRead(b""), "not available at the moment"),
    (http_error(503), r"OPM returned an error \(503\)"),
])
def test_unavailable(fake, failure, message):
    fake(failure)
    with pytest.raises(MembraneError, match=message):
        opm.download_opm("1k4c", None)


@pytest.mark.parametrize("failure", [urllib.error.URLError("down"), http_error(500)])
def test_unavailable_uses_old_cache(fake, tmp_path, failure):
    cached = tmp_path / "1k4c.pdb"
    cached.write_text(OPM_FILE)
    old = time.time() - (opm.CACHE_DAYS + 1) * 86400
    os.utime(cached, (old, old))
    server = fake(failure)
    assert opm.download_opm("1k4c", tmp_path) == OPM_FILE
    assert server.calls == 1  # it was tried first


def test_old_cache_refreshed(fake, tmp_path):
    cached = tmp_path / "1k4c.pdb"
    cached.write_text("old version\n")
    old = time.time() - (opm.CACHE_DAYS + 1) * 86400
    os.utime(cached, (old, old))
    fake(OPM_FILE)
    assert opm.download_opm("1k4c", tmp_path) == OPM_FILE
    assert cached.read_text() == OPM_FILE
