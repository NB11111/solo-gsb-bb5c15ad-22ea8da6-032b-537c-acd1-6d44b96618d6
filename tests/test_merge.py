"""合并核心逻辑测试。"""

from app.merge import (
    KIND_DELETE_VS_EDIT,
    KIND_EDIT_VS_EDIT,
    merge_documents,
)


def run(base, left, right, identity=None):
    merged, conflicts = merge_documents(base, left, right, identity)
    return merged, [c.to_dict() for c in conflicts]


# ---------- 标量 / 对象的基本三方规则 ----------


def test_only_left_changed_is_adopted():
    merged, conflicts = run({"a": 1}, {"a": 2}, {"a": 1})
    assert merged == {"a": 2}
    assert conflicts == []


def test_only_right_changed_is_adopted():
    merged, conflicts = run({"a": 1}, {"a": 1}, {"a": 3})
    assert merged == {"a": 3}
    assert conflicts == []


def test_both_same_change_is_merged():
    merged, conflicts = run({"a": 1}, {"a": 2}, {"a": 2})
    assert merged == {"a": 2}
    assert conflicts == []


def test_both_different_values_conflict_and_keep_base():
    merged, conflicts = run({"a": 1}, {"a": 2}, {"a": 3})
    assert merged == {"a": 1}
    assert conflicts == [
        {
            "pointer": "/a",
            "kind": KIND_EDIT_VS_EDIT,
            "base": 1,
            "left": 2,
            "right": 3,
        }
    ]


def test_delete_vs_edit_conflict_keeps_base():
    merged, conflicts = run({"a": 1, "b": 0}, {"b": 0}, {"a": 2, "b": 0})
    assert merged == {"a": 1, "b": 0}
    assert conflicts == [
        {
            "pointer": "/a",
            "kind": KIND_DELETE_VS_EDIT,
            "base": 1,
            "left": None,
            "right": 2,
        }
    ]


def test_delete_vs_unchanged_adopts_deletion():
    merged, conflicts = run({"a": 1, "b": 0}, {"b": 0}, {"a": 1, "b": 0})
    assert merged == {"b": 0}
    assert conflicts == []


def test_both_deleted_no_conflict():
    merged, conflicts = run({"a": 1}, {}, {})
    assert merged == {}
    assert conflicts == []


def test_key_added_by_one_side():
    merged, conflicts = run({"a": 1}, {"a": 1, "b": 2}, {"a": 1})
    assert merged == {"a": 1, "b": 2}
    assert conflicts == []


def test_both_add_same_key_with_different_values_conflict():
    merged, conflicts = run({}, {"b": 1}, {"b": 2})
    assert merged == {}
    assert conflicts == [
        {
            "pointer": "/b",
            "kind": KIND_EDIT_VS_EDIT,
            "base": None,
            "left": 1,
            "right": 2,
        }
    ]


def test_nested_objects_merge_recursively():
    base = {"svc": {"image": "v1", "replicas": 1}}
    left = {"svc": {"image": "v2", "replicas": 1}}
    right = {"svc": {"image": "v1", "replicas": 3}}
    merged, conflicts = run(base, left, right)
    assert merged == {"svc": {"image": "v2", "replicas": 3}}
    assert conflicts == []


def test_nested_conflict_pointer_uses_json_pointer():
    base = {"svc": {"image": "v1"}}
    left = {"svc": {"image": "v2"}}
    right = {"svc": {"image": "v3"}}
    merged, conflicts = run(base, left, right)
    assert merged == {"svc": {"image": "v1"}}
    assert conflicts[0]["pointer"] == "/svc/image"


def test_pointer_escapes_special_characters_in_keys():
    base = {"a/b~c": 1}
    left = {"a/b~c": 2}
    right = {"a/b~c": 3}
    _, conflicts = run(base, left, right)
    assert conflicts[0]["pointer"] == "/a~1b~0c"


def test_root_level_conflict_has_empty_pointer():
    merged, conflicts = run(1, 2, 3)
    assert merged == 1
    assert conflicts == [
        {"pointer": "", "kind": KIND_EDIT_VS_EDIT, "base": 1, "left": 2, "right": 3}
    ]


def test_type_change_on_both_sides_conflicts():
    merged, conflicts = run({"a": {"x": 1}}, {"a": [1]}, {"a": "s"})
    assert merged == {"a": {"x": 1}}
    assert conflicts[0]["kind"] == KIND_EDIT_VS_EDIT


# ---------- 未指定身份字段的数组:整体值语义 ----------


def test_plain_array_changed_on_one_side():
    merged, conflicts = run({"a": [1, 2]}, {"a": [1, 2, 3]}, {"a": [1, 2]})
    assert merged == {"a": [1, 2, 3]}
    assert conflicts == []


def test_plain_array_both_change_differently_conflict():
    merged, conflicts = run({"a": [1]}, {"a": [1, 2]}, {"a": [1, 3]})
    assert merged == {"a": [1]}
    assert conflicts == [
        {
            "pointer": "/a",
            "kind": KIND_EDIT_VS_EDIT,
            "base": [1],
            "left": [1, 2],
            "right": [1, 3],
        }
    ]


# ---------- 指定身份字段的对象数组 ----------


IDENTITY = {"/servers": "name"}


def test_identity_array_modify_add_delete_and_order():
    base = {
        "servers": [
            {"name": "a", "w": 1},
            {"name": "b", "w": 2},
            {"name": "d", "w": 4},
        ]
    }
    # left:调整顺序、修改 a、删除 d、新增 c
    left = {"servers": [{"name": "c", "w": 3}, {"name": "a", "w": 5}, {"name": "b", "w": 2}]}
    # right:修改 b,其余未动
    right = {"servers": [{"name": "a", "w": 1}, {"name": "b", "w": 9}, {"name": "d", "w": 4}]}
    merged, conflicts = run(base, left, right, IDENTITY)
    # base 既有项保持 base 顺序(a 在 b 前),d 被删除,新增项追加
    assert merged == {
        "servers": [
            {"name": "a", "w": 5},
            {"name": "b", "w": 9},
            {"name": "c", "w": 3},
        ]
    }
    assert conflicts == []


def test_identity_array_new_items_sorted_by_identity():
    base = {"servers": [{"name": "b", "w": 1}]}
    left = {"servers": [{"name": "b", "w": 1}, {"name": "z", "w": 1}, {"name": "m", "w": 1}]}
    # right 也修改了数组,触发按身份合并;新增项 m、z 按身份排序追加
    right = {"servers": [{"name": "b", "w": 2}]}
    merged, conflicts = run(base, left, right, IDENTITY)
    assert [s["name"] for s in merged["servers"]] == ["b", "m", "z"]
    assert merged["servers"][0]["w"] == 2
    assert conflicts == []


def test_identity_array_single_side_change_adopts_that_side_wholesale():
    # 仅 left 变化 -> 整体采纳 left(保持 left 的顺序),不触发按身份合并
    base = {"servers": [{"name": "b", "w": 1}]}
    left = {"servers": [{"name": "z", "w": 1}, {"name": "b", "w": 1}]}
    right = {"servers": [{"name": "b", "w": 1}]}
    merged, conflicts = run(base, left, right, IDENTITY)
    assert [s["name"] for s in merged["servers"]] == ["z", "b"]
    assert conflicts == []


def test_identity_array_item_fields_merge_recursively():
    base = {"servers": [{"name": "a", "x": 1, "y": 1}]}
    left = {"servers": [{"name": "a", "x": 2, "y": 1}]}
    right = {"servers": [{"name": "a", "x": 1, "y": 3}]}
    merged, conflicts = run(base, left, right, IDENTITY)
    assert merged == {"servers": [{"name": "a", "x": 2, "y": 3}]}
    assert conflicts == []


def test_identity_array_item_delete_vs_edit_conflict():
    base = {"servers": [{"name": "a", "w": 1}]}
    left = {"servers": []}
    right = {"servers": [{"name": "a", "w": 2}]}
    merged, conflicts = run(base, left, right, IDENTITY)
    assert merged == {"servers": [{"name": "a", "w": 1}]}
    assert conflicts == [
        {
            "pointer": "/servers/a",
            "kind": KIND_DELETE_VS_EDIT,
            "base": {"name": "a", "w": 1},
            "left": None,
            "right": {"name": "a", "w": 2},
        }
    ]


def test_identity_array_both_add_same_identity_differently_conflict():
    base = {"servers": []}
    left = {"servers": [{"name": "x", "w": 1}]}
    right = {"servers": [{"name": "x", "w": 2}]}
    merged, conflicts = run(base, left, right, IDENTITY)
    assert merged == {"servers": []}
    assert conflicts == [
        {
            "pointer": "/servers/x",
            "kind": KIND_EDIT_VS_EDIT,
            "base": None,
            "left": {"name": "x", "w": 1},
            "right": {"name": "x", "w": 2},
        }
    ]


def test_identity_array_both_add_identical_item():
    base = {"servers": []}
    item = {"name": "x", "w": 1}
    merged, conflicts = run(base, {"servers": [item]}, {"servers": [item]}, IDENTITY)
    assert merged == {"servers": [item]}
    assert conflicts == []


def test_identity_array_field_conflict_inside_item():
    base = {"servers": [{"name": "a", "w": 1}]}
    left = {"servers": [{"name": "a", "w": 2}]}
    right = {"servers": [{"name": "a", "w": 3}]}
    merged, conflicts = run(base, left, right, IDENTITY)
    assert merged == {"servers": [{"name": "a", "w": 1}]}
    assert conflicts[0]["pointer"] == "/servers/a/w"
    assert conflicts[0]["kind"] == KIND_EDIT_VS_EDIT


def test_identity_array_falls_back_when_item_lacks_identity():
    base = {"servers": [{"name": "a"}, {"other": 1}]}
    left = {"servers": [{"name": "a"}, {"other": 2}]}
    right = {"servers": [{"name": "a"}, {"other": 3}]}
    merged, conflicts = run(base, left, right, IDENTITY)
    # 存在缺身份字段的项 -> 整体值语义,三方互异 -> 冲突并保留 base
    assert merged == base
    assert conflicts[0]["pointer"] == "/servers"
    assert conflicts[0]["kind"] == KIND_EDIT_VS_EDIT


def test_nested_identity_array_inside_items():
    identity = {"/servers": "name", "/servers/a/ports": "port"}
    base = {"servers": [{"name": "a", "ports": [{"port": 80, "proto": "tcp"}]}]}
    left = {"servers": [{"name": "a", "ports": [{"port": 80, "proto": "udp"}]}]}
    right = {"servers": [{"name": "a", "ports": [{"port": 80, "proto": "tcp"}, {"port": 443, "proto": "tcp"}]}]}
    merged, conflicts = run(base, left, right, identity)
    assert merged == {
        "servers": [
            {
                "name": "a",
                "ports": [
                    {"port": 80, "proto": "udp"},
                    {"port": 443, "proto": "tcp"},
                ],
            }
        ]
    }
    assert conflicts == []


# ---------- 确定性 / 排序 ----------


def test_conflicts_sorted_by_pointer():
    base = {"b": 1, "a": 1, "c": {"d": 1}}
    left = {"b": 2, "a": 2, "c": {"d": 2}}
    right = {"b": 3, "a": 3, "c": {"d": 3}}
    _, conflicts = run(base, left, right)
    pointers = [c["pointer"] for c in conflicts]
    assert pointers == sorted(pointers)
    assert pointers == ["/a", "/b", "/c/d"]


def test_merge_is_deterministic():
    base = {"s": [{"name": "a", "v": 1}], "x": 1, "y": [1, 2]}
    left = {"s": [{"name": "a", "v": 2}, {"name": "c", "v": 0}], "x": 2, "y": [1]}
    right = {"s": [{"name": "a", "v": 3}], "x": 2, "y": [1, 2, 3]}
    identity = {"/s": "name"}
    first = merge_documents(base, left, right, identity)
    second = merge_documents(base, left, right, identity)
    assert first[0] == second[0]
    assert [c.to_dict() for c in first[1]] == [c.to_dict() for c in second[1]]
