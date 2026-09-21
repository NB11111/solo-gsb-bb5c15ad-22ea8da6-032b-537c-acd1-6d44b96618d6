"""请求 / 响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class MergeRequest(BaseModel):
    """POST /merge 请求体。

    - base / left / right:任意 JSON 值(通常为对象)
    - array_identity:可选,JSON Pointer -> 身份字段名,如 {"/servers": "name"}
    """

    base: Any
    left: Any
    right: Any
    array_identity: dict[str, str] = Field(default_factory=dict)

    @field_validator("array_identity")
    @classmethod
    def _validate_array_identity(cls, value: dict[str, str]) -> dict[str, str]:
        for pointer, field in value.items():
            if pointer != "" and not pointer.startswith("/"):
                raise ValueError(
                    f"array_identity 的键必须是 JSON Pointer(空串或以 '/' 开头): {pointer!r}"
                )
            if not isinstance(field, str) or not field:
                raise ValueError("array_identity 的身份字段名必须是非空字符串")
        return value


class ConflictModel(BaseModel):
    """一次冲突。某一侧不存在该值(删除 / 从未存在)时对应字段为 null。"""

    pointer: str
    kind: str
    base: Any = None
    left: Any = None
    right: Any = None


class MergeResponse(BaseModel):
    merged: Any = None
    conflicts: list[ConflictModel]
