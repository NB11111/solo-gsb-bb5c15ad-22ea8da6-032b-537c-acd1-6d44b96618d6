"""三方 JSON 合并核心逻辑。

以 base 为基准,对比 left / right 两侧,按 JSON Pointer(RFC 6901)递归合并:

- 仅单侧发生变化        -> 自动采纳该侧
- 双侧发生相同变化       -> 采纳该变化
- 一侧删除而另一侧修改   -> 保留 base,记录 ``delete_vs_edit`` 冲突
- 双侧改为不同的值       -> 保留 base,记录 ``edit_vs_edit`` 冲突
- 指定了身份字段的对象数组 -> 按身份合并增删改:base 既有项保持原顺序,
  新增项按身份排序追加;数组项在冲突指针中以身份值作为路径段
- 未指定身份字段的数组   -> 视为整体值(任一差异都按整值处理)

相同输入必然产生相同输出(确定性):对象键按 base 顺序 + 其余键字典序,
新增数组项按身份的 JSON 序列化字典序,冲突按指针字典序排列。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class _Missing:
    """哨兵值:表示某一侧不存在该值(被删除或从未存在),与 JSON null 区分。"""

    def __repr__(self) -> str:  # pragma: no cover
        return "MISSING"


MISSING = _Missing()

KIND_DELETE_VS_EDIT = "delete_vs_edit"
KIND_EDIT_VS_EDIT = "edit_vs_edit"


@dataclass
class Conflict:
    """一次合并冲突。base/left/right 为 MISSING 时表示该侧不存在此值。"""

    pointer: str
    kind: str
    base: Any
    left: Any
    right: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "pointer": self.pointer,
            "kind": self.kind,
            "base": None if self.base is MISSING else self.base,
            "left": None if self.left is MISSING else self.left,
            "right": None if self.right is MISSING else self.right,
        }


def escape_segment(segment: str) -> str:
    """按 RFC 6901 转义单个指针路径段。"""

    return segment.replace("~", "~0").replace("/", "~1")


def join_pointer(pointer: str, segment: str) -> str:
    return f"{pointer}/{escape_segment(segment)}"


def merge_documents(
    base: Any,
    left: Any,
    right: Any,
    array_identity: dict[str, str] | None = None,
) -> tuple[Any, list[Conflict]]:
    """合并 base/left/right,返回 (merged, conflicts)。

    ``array_identity`` 形如 ``{"/servers": "name"}``:键为数组的 JSON Pointer,
    值为数组对象项的身份字段名。冲突列表按 pointer 字典序排序后返回。
    """

    identity_map = {
        _normalize_pointer(pointer): field
        for pointer, field in (array_identity or {}).items()
    }
    conflicts: list[Conflict] = []
    merged = _merge(base, left, right, "", identity_map, conflicts)
    conflicts.sort(key=lambda c: c.pointer)
    return (None if merged is MISSING else merged), conflicts


def _normalize_pointer(pointer: str) -> str:
    while len(pointer) > 1 and pointer.endswith("/"):
        pointer = pointer[:-1]
    return pointer


def _merge(
    base: Any,
    left: Any,
    right: Any,
    pointer: str,
    identity_map: dict[str, str],
    conflicts: list[Conflict],
) -> Any:
    # 双侧一致(含双侧都删除)-> 直接采纳
    if left == right:
        return left
    # 仅 right 变化 -> 采纳 right
    if left == base:
        return right
    # 仅 left 变化 -> 采纳 left
    if right == base:
        return left
    # 三方互不相同:可递归的结构继续递归,否则记录冲突并保留 base
    if isinstance(base, dict) and isinstance(left, dict) and isinstance(right, dict):
        return _merge_objects(base, left, right, pointer, identity_map, conflicts)
    if isinstance(base, list) and isinstance(left, list) and isinstance(right, list):
        id_field = identity_map.get(pointer)
        if id_field:
            merged = _merge_identity_array(
                base, left, right, pointer, id_field, identity_map, conflicts
            )
            if merged is not None:
                return merged
    conflicts.append(Conflict(pointer, _conflict_kind(left, right), base, left, right))
    return base


def _conflict_kind(left: Any, right: Any) -> str:
    if left is MISSING or right is MISSING:
        return KIND_DELETE_VS_EDIT
    return KIND_EDIT_VS_EDIT


def _merge_objects(
    base: dict,
    left: dict,
    right: dict,
    pointer: str,
    identity_map: dict[str, str],
    conflicts: list[Conflict],
) -> dict:
    merged: dict[str, Any] = {}
    # 键顺序:base 既有顺序在前,仅出现在 left/right 的键按字典序追加,保证确定性
    extra_keys = sorted((set(left) | set(right)) - set(base))
    for key in list(base.keys()) + extra_keys:
        value = _merge(
            base.get(key, MISSING),
            left.get(key, MISSING),
            right.get(key, MISSING),
            join_pointer(pointer, key),
            identity_map,
            conflicts,
        )
        if value is not MISSING:
            merged[key] = value
    return merged


def _identity_of(item: Any, id_field: str) -> Any:
    if isinstance(item, dict) and id_field in item:
        return item[id_field]
    return MISSING


def _identity_sort_key(identity: Any) -> str:
    return json.dumps(identity, sort_keys=True, ensure_ascii=False)


def _index_by_identity(array: list, id_field: str) -> tuple[dict, list] | None:
    """把数组项按身份字段索引。任一项缺少身份字段或身份重复时返回 None。"""

    indexed: dict[str, tuple[Any, Any]] = {}
    order: list[str] = []
    for item in array:
        identity = _identity_of(item, id_field)
        if identity is MISSING:
            return None
        key = _identity_sort_key(identity)
        if key in indexed:
            return None
        indexed[key] = (identity, item)
        order.append(key)
    return indexed, order


def _merge_identity_array(
    base: list,
    left: list,
    right: list,
    pointer: str,
    id_field: str,
    identity_map: dict[str, str],
    conflicts: list[Conflict],
) -> list | None:
    """按身份字段合并对象数组。无法按身份索引时返回 None(回退为整体值语义)。"""

    b_idx = _index_by_identity(base, id_field)
    l_idx = _index_by_identity(left, id_field)
    r_idx = _index_by_identity(right, id_field)
    if b_idx is None or l_idx is None or r_idx is None:
        return None
    b_map, b_order = b_idx
    l_map, _ = l_idx
    r_map, _ = r_idx

    merged: list[Any] = []
    # base 既有项:保持 base 中的顺序逐项三方合并
    for key in b_order:
        identity, b_item = b_map[key]
        l_item = l_map[key][1] if key in l_map else MISSING
        r_item = r_map[key][1] if key in r_map else MISSING
        value = _merge(
            b_item,
            l_item,
            r_item,
            join_pointer(pointer, str(identity)),
            identity_map,
            conflicts,
        )
        if value is not MISSING:
            merged.append(value)
    # 新增项:按身份排序后追加
    for key in sorted((set(l_map) | set(r_map)) - set(b_map)):
        identity = (l_map.get(key) or r_map[key])[0]
        l_item = l_map[key][1] if key in l_map else MISSING
        r_item = r_map[key][1] if key in r_map else MISSING
        value = _merge(
            MISSING,
            l_item,
            r_item,
            join_pointer(pointer, str(identity)),
            identity_map,
            conflicts,
        )
        if value is not MISSING:
            merged.append(value)
    return merged
