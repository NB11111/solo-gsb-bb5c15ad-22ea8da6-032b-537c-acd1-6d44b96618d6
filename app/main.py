"""FastAPI 应用入口。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from .config import max_request_bytes_from_env
from .merge import merge_documents
from .models import MergeRequest, MergeResponse


def _too_large_response(limit: int) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={"detail": f"请求体超过大小上限({limit} 字节)"},
    )


def create_app(max_request_bytes: int | None = None) -> FastAPI:
    """创建应用。``max_request_bytes`` 为 None 时读取环境变量 MAX_REQUEST_BYTES。"""

    limit = (
        max_request_bytes
        if max_request_bytes is not None
        else max_request_bytes_from_env()
    )

    class LimitedBodyRoute(APIRoute):
        """在路由处理前强制检查请求体大小,超限返回 413。"""

        def get_route_handler(self):
            original = super().get_route_handler()

            async def handler(request: Request):
                content_length = request.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared = int(content_length)
                    except ValueError:
                        declared = None
                    if declared is not None and declared > limit:
                        return _too_large_response(limit)
                body = await request.body()
                if len(body) > limit:
                    return _too_large_response(limit)
                return await original(request)

            return handler

    app = FastAPI(
        title="三方 JSON 合并 API",
        version="1.0.0",
        description="面向配置审阅的三方 JSON 合并服务:自动合并单侧/同值变更,报告冲突。",
    )
    app.router.route_class = LimitedBodyRoute

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/merge", response_model=MergeResponse)
    async def merge_endpoint(payload: MergeRequest) -> MergeResponse:
        merged, conflicts = merge_documents(
            payload.base, payload.left, payload.right, payload.array_identity
        )
        return MergeResponse(
            merged=merged,
            conflicts=[conflict.to_dict() for conflict in conflicts],
        )

    return app


app = create_app()
