"""HTTP API 测试。"""

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_MAX_REQUEST_BYTES, max_request_bytes_from_env
from app.main import create_app


@pytest.fixture()
def client():
    return TestClient(create_app())


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_merge_happy_path(client):
    resp = client.post(
        "/merge",
        json={
            "base": {"a": 1, "b": 1},
            "left": {"a": 2, "b": 1},
            "right": {"a": 1, "b": 3},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["merged"] == {"a": 2, "b": 3}
    assert body["conflicts"] == []


def test_merge_returns_sorted_conflicts(client):
    resp = client.post(
        "/merge",
        json={
            "base": {"b": 1, "a": 1},
            "left": {"b": 2, "a": 2},
            "right": {"b": 3, "a": 3},
        },
    )
    assert resp.status_code == 200
    conflicts = resp.json()["conflicts"]
    assert [c["pointer"] for c in conflicts] == ["/a", "/b"]
    assert all(
        set(c) == {"pointer", "kind", "base", "left", "right"} for c in conflicts
    )


def test_merge_with_array_identity(client):
    resp = client.post(
        "/merge",
        json={
            "base": {"servers": [{"name": "a", "w": 1}]},
            "left": {"servers": [{"name": "a", "w": 2}]},
            "right": {"servers": [{"name": "a", "w": 1}, {"name": "b", "w": 3}]},
            "array_identity": {"/servers": "name"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["merged"] == {
        "servers": [{"name": "a", "w": 2}, {"name": "b", "w": 3}]
    }
    assert body["conflicts"] == []


def test_merge_missing_field_returns_422(client):
    resp = client.post("/merge", json={"base": {}, "left": {}})
    assert resp.status_code == 422


def test_merge_invalid_array_identity_returns_422(client):
    resp = client.post(
        "/merge",
        json={
            "base": {},
            "left": {},
            "right": {},
            "array_identity": {"servers": "name"},
        },
    )
    assert resp.status_code == 422


def test_request_body_too_large_returns_413():
    app = create_app(max_request_bytes=64)
    client = TestClient(app)
    payload = {"base": {"k": "x" * 100}, "left": {}, "right": {}}
    resp = client.post("/merge", json=payload)
    assert resp.status_code == 413
    assert "64" in resp.json()["detail"]


def test_request_body_within_limit_is_accepted():
    app = create_app(max_request_bytes=4096)
    client = TestClient(app)
    resp = client.post("/merge", json={"base": {}, "left": {}, "right": {}})
    assert resp.status_code == 200


def test_max_request_bytes_from_env(monkeypatch):
    monkeypatch.delenv("MAX_REQUEST_BYTES", raising=False)
    assert max_request_bytes_from_env() == DEFAULT_MAX_REQUEST_BYTES
    monkeypatch.setenv("MAX_REQUEST_BYTES", "1024")
    assert max_request_bytes_from_env() == 1024
    monkeypatch.setenv("MAX_REQUEST_BYTES", "not-a-number")
    assert max_request_bytes_from_env() == DEFAULT_MAX_REQUEST_BYTES
