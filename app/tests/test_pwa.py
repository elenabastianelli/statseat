"""The backend serves the built PWA on the same origin as the API (task C-1, the container of infra/)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.engine.config import EngineConfig
from app.engine.engine import Engine
from app.engine.server import create_app
from app.engine.sources.fake import load_fake


def _client(monkeypatch, dist) -> TestClient:
    monkeypatch.setenv("STATSEAT_WEB_DIST", str(dist))
    return TestClient(create_app(Engine(load_fake(), EngineConfig(speed=10.0))))


def test_pwa_and_api_on_one_origin(monkeypatch, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>statseat</title>")
    (tmp_path / "assets" / "app-1a2b.js").write_text("console.log(1)")
    (tmp_path / "sw.js").write_text("self")
    (tmp_path.parent / "secret.txt").write_text("no")
    c = _client(monkeypatch, tmp_path)

    assert c.get("/").text.startswith("<!doctype html>")
    assert "statseat" in c.get("/live/anything").text  # client-side routes get the app
    assert "immutable" in c.get("/assets/app-1a2b.js").headers["cache-control"]
    assert c.get("/sw.js").headers["cache-control"] == "no-cache"
    assert "statseat" in c.get("/../secret.txt").text  # never a file outside the build
    assert c.get("/api/health").json()["status"] == "ok"  # the API wins over the app
    assert c.get("/api/recap/pdf").json()["code"] == "invalid_token"  # L-2g: the PDF route is not the app's
    r = c.get("/api/does-not-exist")
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_no_build_no_pwa(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path / "missing")
    assert c.get("/").status_code == 404
    assert c.get("/api/health").status_code == 200
