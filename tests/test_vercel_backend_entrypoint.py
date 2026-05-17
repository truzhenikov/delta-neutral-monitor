from __future__ import annotations

from pathlib import Path
import importlib.util
import json

import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_declares_vercel_entrypoint_for_fastapi_app() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["tool"]["vercel"]["entrypoint"] == "src.main:app"


def test_vercel_api_index_exists_and_exports_app() -> None:
    entrypoint = ROOT / "api" / "index.py"
    assert entrypoint.exists()

    spec = importlib.util.spec_from_file_location("vercel_api_index", entrypoint)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert getattr(module, "app", None) is not None


def test_vercel_json_routes_health_and_v1_to_python_function() -> None:
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    routes = config["routes"]

    assert {"src": "/health", "dest": "api/index.py"} in routes
    assert {"src": "/v1/(.*)", "dest": "api/index.py"} in routes
