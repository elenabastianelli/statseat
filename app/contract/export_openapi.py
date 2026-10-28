"""Export the OpenAPI document of the contract: python -m app.contract.export_openapi"""

from __future__ import annotations

import json
from pathlib import Path

from .mock import create_app

OUT = Path(__file__).resolve().parent / "openapi.json"


def openapi_text() -> str:
    # Python 3.13 renamed the 422 phrase ("Unprocessable Content"): keep 3.12's, the server's (Dockerfile), on any Python
    text = json.dumps(create_app().openapi(), indent=2, ensure_ascii=False) + "\n"
    return text.replace('"description": "Unprocessable Content"', '"description": "Unprocessable Entity"')


def main() -> None:
    OUT.write_text(openapi_text(), encoding="utf-8")
    print(f"wrote {OUT.name}")


if __name__ == "__main__":
    main()
