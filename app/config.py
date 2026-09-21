"""服务配置(通过环境变量覆盖)。"""

from __future__ import annotations

import os

#: 请求体大小上限的环境变量名
ENV_MAX_REQUEST_BYTES = "MAX_REQUEST_BYTES"

#: 默认请求体大小上限:5 MiB
DEFAULT_MAX_REQUEST_BYTES = 5 * 1024 * 1024


def max_request_bytes_from_env() -> int:
    raw = os.environ.get(ENV_MAX_REQUEST_BYTES)
    if raw is None:
        return DEFAULT_MAX_REQUEST_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_REQUEST_BYTES
    return value if value >= 0 else DEFAULT_MAX_REQUEST_BYTES
