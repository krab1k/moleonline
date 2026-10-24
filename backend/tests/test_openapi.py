import importlib.util
from pathlib import Path

TOOL = Path(__file__).parent.parent / "tools" / "openapi.py"


def test_openapi_json_is_current():
    """The web's types are generated from openapi.json: after changing the API, run uv run python tools/openapi.py"""
    spec = importlib.util.spec_from_file_location("openapi_tool", TOOL)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert tool.OPENAPI.read_text() == tool.describe(), "openapi.json is out of date: uv run python tools/openapi.py"
