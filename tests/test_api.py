"""HTTP-level tests: /health, /merge, request-size enforcement."""

from __future__ import annotations

import os

os.environ.setdefault("MAX_REQUEST_SIZE_BYTES", "10M")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import app
from app.middleware import RequestSizeLimitMiddleware


client = TestClient(app)


def test_health_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_merge_endpoint_full_envelope():
    payload = {
        "base": {"a": 1, "b": 2, "srv": [{"id": "x", "v": 1}]},
        "left": {"a": 10, "b": 2, "srv": [{"id": "x", "v": 1}, {"id": "y", "v": 2}]},
        "right": {"a": 1, "b": 20, "srv": [{"id": "x", "v": 3}]},
        "arrayIdentity": {"/srv": "id"},
    }
    response = client.post("/merge", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["merged"] == {
        "a": 10,
        "b": 20,
        "srv": [
            {"id": "x", "v": 3},  # right-only edit -> accepted
            {"id": "y", "v": 2},  # left-only add, sorted after x
        ],
    }
    assert body["conflicts"] == []


def test_merge_accepts_snake_case_alias_too():
    payload = {
        "base": {"srv": []},
        "left": {"srv": [{"id": "a"}]},
        "right": {"srv": []},
        "array_identity": {"/srv": "id"},
    }
    response = client.post("/merge", json=payload)
    assert response.status_code == 200
    assert response.json()["merged"] == {"srv": [{"id": "a"}]}


def test_merge_defaults_array_identity_to_empty():
    response = client.post(
        "/merge",
        json={"base": [1, 2], "left": [1, 2, 3], "right": [1, 2]},
    )
    assert response.status_code == 200
    assert response.json()["merged"] == [1, 2, 3]
    assert response.json()["conflicts"] == []


def test_unknown_field_is_rejected():
    response = client.post(
        "/merge",
        json={"base": 1, "left": 1, "right": 1, "bogus": 1},
    )
    assert response.status_code == 422


def test_invalid_pointer_returns_400():
    response = client.post(
        "/merge",
        json={
            "base": {"srv": []},
            "left": {"srv": []},
            "right": {"srv": []},
            "arrayIdentity": {"srv": "id"},  # missing leading slash
        },
    )
    assert response.status_code == 400
    assert "arrayIdentity" in response.json()["detail"]


def test_invalid_identity_field_returns_400():
    response = client.post(
        "/merge",
        json={
            "base": {"srv": []},
            "left": {"srv": []},
            "right": {"srv": []},
            "arrayIdentity": {"/srv": ""},
        },
    )
    assert response.status_code == 400


# ------------------------------------------------------------- size limiting


@pytest.fixture
def limited_client():
    small = FastAPI()

    @small.post("/echo")
    async def echo(request: Request) -> dict:
        return {"size": len(await request.body())}

    small.add_middleware(RequestSizeLimitMiddleware, max_body_size=100)
    return TestClient(small)


def test_request_under_limit_passes(limited_client):
    response = limited_client.post("/echo", content=b"x" * 100)
    assert response.status_code == 200
    assert response.json() == {"size": 100}


def test_request_over_declared_content_length_rejected(limited_client):
    response = limited_client.post(
        "/echo",
        content=b"x" * 101,
        headers={"Content-Length": "101"},
    )
    assert response.status_code == 413
    assert "maximum allowed size" in response.json()["detail"]


def test_chunked_oversized_body_rejected_even_without_content_length():
    small = FastAPI()

    @small.post("/echo")
    async def echo(request: Request):
        return {"size": len(await request.body())}

    small.add_middleware(RequestSizeLimitMiddleware, max_body_size=100)

    def chunks():
        yield b"x" * 60
        yield b"x" * 60  # total 120 > 100

    with TestClient(small) as tc:
        response = tc.post(
            "/echo",
            content=chunks(),
            headers={"Transfer-Encoding": "chunked"},
        )
    assert response.status_code == 413


def test_size_setting_parses_suffixes():
    assert _parse("10M") == 10 * 1024 * 1024
    assert _parse("256K") == 256 * 1024
    assert _parse("1G") == 1024**3
    assert _parse("0") == 0


def _parse(value: str) -> int:
    from app.config import _parse_size

    return _parse_size(value)
