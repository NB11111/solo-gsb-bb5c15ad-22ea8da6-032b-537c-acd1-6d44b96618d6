"""FastAPI application: three-way JSON merge for configuration review."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .config import settings
from .merger import Merger, parse_pointer
from .middleware import RequestSizeLimitMiddleware
from .models import Conflict, MergeRequest, MergeResponse

app = FastAPI(
    title="Three-way JSON Merge API",
    description=(
        "Merges two JSON revisions of a configuration against their common "
        "base. Changes on one side are accepted automatically; divergent "
        "changes and delete-versus-modify cases keep the base value and are "
        "reported as conflicts located by JSON Pointer (RFC 6901)."
    ),
    version="1.0.0",
)
app.add_middleware(
    RequestSizeLimitMiddleware,
    max_body_size=settings.max_request_size_bytes,
)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/merge",
    response_model=MergeResponse,
    tags=["merge"],
    summary="Three-way merge of JSON documents",
)
async def merge(request: MergeRequest) -> MergeResponse:
    # Validate pointers up front so malformed configuration produces a clear
    # 4xx response instead of an opaque error.
    for pointer in request.array_identity:
        try:
            parse_pointer(pointer)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"invalid arrayIdentity pointer {pointer!r}: {exc}",
            ) from exc

    try:
        merger = Merger(request.array_identity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    merged, raw_conflicts = merger.merge(request.base, request.left, request.right)
    conflicts = [
        Conflict(
            pointer=conflict.path,
            kind=conflict.kind,
            base=conflict.base,
            left=conflict.left,
            right=conflict.right,
        )
        for conflict in raw_conflicts
    ]
    return MergeResponse(merged=merged, conflicts=conflicts)
