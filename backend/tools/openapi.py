"""Writes the API's OpenAPI description to openapi.json next to pyproject.toml, from which the web generates its types
(npm run api-types in web/). tests/test_openapi.py fails while the file is out of date.

usage: uv run python tools/openapi.py
"""

import json
from pathlib import Path

from moleonline.main import create_app

OPENAPI = Path(__file__).parent.parent / "openapi.json"


def describe() -> str:
    return json.dumps(create_app().openapi(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    OPENAPI.write_text(describe())
