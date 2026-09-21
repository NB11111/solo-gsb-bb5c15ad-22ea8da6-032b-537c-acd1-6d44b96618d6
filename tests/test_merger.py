"""Tests for the three-way merge engine (no HTTP layer)."""

from __future__ import annotations

import pytest

from app.merger import Merger, json_equal, parse_pointer


def merge(base, left, right, identity=None):
    merged, conflicts = Merger(identity).merge(base, left, right)
    return merged, [
        (c.path, c.kind, c.base, c.left, c.right) for c in conflicts
    ]


# ----------------------------------------------------------------- acceptance


def test_unrelated_field_changes_are_both_accepted():
    merged, conflicts = merge(
        {"a": 1, "b": 2},
        {"a": 10, "b": 2},
        {"a": 1, "b": 20},
    )
    assert merged == {"a": 10, "b": 20}
    assert conflicts == []


def test_identical_change_on_both_sides_is_merged_once():
    merged, conflicts = merge({"a": 1}, {"a": 9}, {"a": 9})
    assert merged == {"a": 9}
    assert conflicts == []


def test_deletion_on_both_sides_is_accepted():
    merged, conflicts = merge({"a": 1, "b": 2}, {"b": 2}, {"b": 2})
    assert merged == {"b": 2}
    assert conflicts == []


def test_deletion_on_one_side_with_other_unchanged_is_accepted():
    merged, conflicts = merge({"a": 1, "b": 2}, {"a": 1, "b": 2}, {"b": 2})
    assert merged == {"b": 2}
    assert conflicts == []


def test_single_sided_addition_is_accepted():
    merged, conflicts = merge({"a": 1}, {"a": 1}, {"a": 1, "b": 2})
    assert merged == {"a": 1, "b": 2}
    assert conflicts == []


def test_null_is_a_real_value_not_a_deletion():
    merged, conflicts = merge({"a": 1}, {"a": None}, {"a": None})
    assert merged == {"a": None}
    assert conflicts == []


# ------------------------------------------------------------------ conflicts


def test_both_modified_scalar_conflict_keeps_base():
    merged, conflicts = merge({"a": 1}, {"a": 2}, {"a": 3})
    assert merged == {"a": 1}
    assert conflicts == [("/a", "both_modified", 1, 2, 3)]


def test_delete_vs_modify_keeps_base_both_directions():
    merged, conflicts = merge({"a": 1}, {}, {"a": 3})
    assert merged == {"a": 1}
    assert conflicts == [("/a", "delete_vs_modify", 1, None, 3)]

    merged, conflicts = merge({"a": 1}, {"a": 3}, {})
    assert merged == {"a": 1}
    assert conflicts == [("/a", "delete_vs_modify", 1, 3, None)]


def test_null_vs_scalar_is_a_both_modified_conflict():
    merged, conflicts = merge({"a": 1}, {"a": None}, {"a": 2})
    assert merged == {"a": 1}
    assert conflicts == [("/a", "both_modified", 1, None, 2)]


def test_both_added_with_different_values_conflicts():
    merged, conflicts = merge({}, {"a": 1}, {"a": 2})
    assert merged == {"a": 1}
    assert conflicts == [("/a", "both_added", None, 1, 2)]


def test_field_level_conflicts_inside_object_merge():
    # Both sides edit the same nested object: identical edits are merged,
    # independent edits are both accepted, only divergent edits conflict.
    base = {"o": {"same": 1, "x": 1, "y": 1, "z": 1}}
    left = {"o": {"same": 9, "x": 10, "y": 1, "z": 1}}
    right = {"o": {"same": 9, "x": 1, "y": 20, "z": 30}}
    merged, conflicts = merge(base, left, right)
    assert merged == {
        "o": {"same": 9, "x": 10, "y": 20, "z": 30}
    }
    assert conflicts == []

    # And when one field genuinely diverges, only that field is reported.
    base = {"o": {"x": 1, "y": 1}}
    left = {"o": {"x": 11, "y": 1}}
    right = {"o": {"x": 12, "y": 20}}
    merged, conflicts = merge(base, left, right)
    assert merged == {"o": {"x": 1, "y": 20}}
    assert conflicts == [("/o/x", "both_modified", 1, 11, 12)]


def test_conflict_pointers_are_sorted():
    base = {"m": {"b": 1, "a": 1}, "z": 1, "a": 1}
    left = {"m": {"b": 2, "a": 2}, "z": 2, "a": 2}
    right = {"m": {"b": 3, "a": 3}, "z": 3, "a": 3}
    _, conflicts = merge(base, left, right)
    assert [c[0] for c in conflicts] == ["/a", "/m/a", "/m/b", "/z"]


def test_pointer_tokens_with_slash_and_tilde_are_escaped():
    merged, conflicts = merge(
        {"a/b": 1, "c~d": 1},
        {"a/b": 2, "c~d": 2},
        {"a/b": 3, "c~d": 3},
    )
    assert merged == {"a/b": 1, "c~d": 1}
    assert {c[0] for c in conflicts} == {"/a~1b", "/c~0d"}


def test_root_level_conflict_uses_empty_pointer():
    merged, conflicts = merge(1, 2, 3)
    assert merged == 1
    assert conflicts == [("", "both_modified", 1, 2, 3)]


def test_bool_never_equals_number():
    assert json_equal(True, 1) is False
    merged, _ = merge({"a": True}, {"a": 1}, {"a": 1})
    # Both sides made the same (Python-loose but JSON-distinct) change.
    assert merged == {"a": 1}


# ------------------------------------------------------------ plain arrays


def test_array_without_identity_is_an_atomic_value():
    merged, conflicts = merge({"a": [1, 2]}, {"a": [1, 2, 3]}, {"a": [1, 2]})
    assert merged == {"a": [1, 2, 3]}
    assert conflicts == []

    merged, conflicts = merge({"a": [1, 2]}, {"a": [1, 2, 3]}, {"a": [9]})
    assert merged == {"a": [1, 2]}
    assert conflicts == [("/a", "both_modified", [1, 2], [1, 2, 3], [9])]


# -------------------------------------------------------- identity arrays


def test_identity_array_merges_adds_removes_and_edits():
    base = {
        "srv": [
            {"id": "a", "v": 1},
            {"id": "b", "v": 2},
            {"id": "c", "v": 3},
        ]
    }
    left = {
        "srv": [
            {"id": "a", "v": 10},  # edited on left
            {"id": "c", "v": 3},
            {"id": "e", "v": 9},
        ]
    }
    right = {
        "srv": [
            {"id": "a", "v": 1},
            {"id": "b", "v": 20},  # edited on right
            {"id": "d", "v": 8},  # added on right
        ]
    }
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    # a: left-only edit -> accepted.
    # b: left deleted it, right modified it -> base retained + conflict.
    # c: right deleted it, left untouched -> deletion accepted.
    # d/e: one-sided adds, sorted by identity.
    assert merged == {
        "srv": [
            {"id": "a", "v": 10},
            {"id": "b", "v": 2},
            {"id": "d", "v": 8},
            {"id": "e", "v": 9},
        ]
    }
    assert conflicts == [
        ("/srv/1", "delete_vs_modify",
         {"id": "b", "v": 2}, None, {"id": "b", "v": 20})
    ]


def test_identity_array_preserves_base_order_and_sorts_new_items():
    base = {"srv": [{"id": "z"}, {"id": "a"}, {"id": "m"}]}
    left = {"srv": [{"id": "z"}, {"id": "a"}, {"id": "m"}, {"id": 9}, {"id": 1}]}
    right = {"srv": [{"id": "z"}, {"id": "a"}, {"id": "m"}, {"id": "b"}]}
    merged, _ = merge(base, left, right, {"/srv": "id"})
    ids = [item["id"] for item in merged["srv"]]
    # Existing items keep base order; new items are sorted by identity.
    assert ids == ["z", "a", "m", 1, 9, "b"]


def test_identity_new_item_ordering_by_type():
    # null < booleans < numbers < strings.
    base = {"srv": [{"id": "base"}]}
    left = {
        "srv": [
            {"id": "base"},
            {"id": "z"},
            {"id": 2},
            {"id": True},
            {"id": None},
        ]
    }
    right = {"srv": [{"id": "base"}]}
    merged, _ = merge(base, left, right, {"/srv": "id"})
    assert [x["id"] for x in merged["srv"]] == ["base", None, True, 2, "z"]


def test_identity_array_delete_vs_modify_conflict_keeps_base_item():
    base = {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    left = {"srv": [{"id": "b", "v": 2}]}  # deletes a
    right = {"srv": [{"id": "a", "v": 99}, {"id": "b", "v": 2}]}  # edits a
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert merged == {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    assert conflicts == [
        ("/srv/0", "delete_vs_modify",
         {"id": "a", "v": 1}, None, {"id": "a", "v": 99})
    ]


def test_identity_array_field_conflict_pointer_survives_earlier_deletes():
    # c (index 2) is deleted by right; b (index 1) has a field conflict.
    base = {"srv": [{"id": "a"}, {"id": "b", "v": 1}, {"id": "c"}]}
    left = {"srv": [{"id": "a"}, {"id": "b", "v": 11}]}
    right = {"srv": [{"id": "a"}, {"id": "b", "v": 12}]}
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert merged == {"srv": [{"id": "a"}, {"id": "b", "v": 1}]}
    assert [c[0] for c in conflicts] == ["/srv/1/v"]
    assert conflicts[0][1] == "both_modified"


def test_identity_array_same_new_id_added_differently_conflicts():
    base = {"srv": []}
    left = {"srv": [{"id": "n", "v": 1}]}
    right = {"srv": [{"id": "n", "v": 2}]}
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert merged == {"srv": [{"id": "n", "v": 1}]}
    assert conflicts == [
        ("/srv", "both_added", None, {"id": "n", "v": 1}, {"id": "n", "v": 2})
    ]


def test_nested_identity_array_via_wildcard_pointer():
    base = {
        "servers": [
            {"id": "s1", "disks": [{"name": "d1", "size": 10}, {"name": "d2", "size": 20}]}
        ]
    }
    left = {
        "servers": [
            {"id": "s1", "disks": [{"name": "d1", "size": 100}, {"name": "d2", "size": 20}]}
        ]
    }
    right = {
        "servers": [
            {"id": "s1", "disks": [{"name": "d1", "size": 10}, {"name": "d3", "size": 30}]}
        ]
    }
    merged, conflicts = merge(
        base, left, right,
        {"/servers": "id", "/servers/*/disks": "name"},
    )
    assert merged == {
        "servers": [
            {"id": "s1", "disks": [
                {"name": "d1", "size": 100},
                {"name": "d3", "size": 30},
            ]}
        ]
    }
    assert conflicts == []


def test_non_eligible_identity_array_falls_back_to_atomic():
    # Scalar items cannot carry the identity field.
    base = {"srv": [1, 2]}
    left = {"srv": [1, 2, 3]}
    right = {"srv": [1, 2, 4]}
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert merged == {"srv": [1, 2]}
    assert [c[1] for c in conflicts] == ["both_modified"]


def test_duplicate_identity_on_a_side_falls_back_to_atomic():
    base = {"srv": [{"id": "a"}]}
    left = {"srv": [{"id": "a"}, {"id": "a"}]}
    right = {"srv": [{"id": "b"}]}
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert merged == {"srv": [{"id": "a"}]}
    assert [c[1] for c in conflicts] == ["both_modified"]


# ------------------------------------------------------------- determinism


def test_merge_is_deterministic():
    base = {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    left = {"srv": [{"id": "b", "v": 2}, {"id": "z"}, {"id": "a", "v": 11}]}
    right = {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 22}, {"id": "a2"}]}
    identity = {"/srv": "id"}
    first = Merger(identity).merge(base, left, right)
    for _ in range(5):
        assert Merger(identity).merge(base, left, right) == first


# ----------------------------------------------------------------- pointers


def test_parse_pointer_forms():
    assert parse_pointer("") == ()
    assert parse_pointer("/a/b") == ("a", "b")
    assert parse_pointer("/a~1b/c~0d") == ("a/b", "c~d")
    assert parse_pointer("/servers/*/disks") == ("servers", "*", "disks")


def test_parse_pointer_rejects_bad_forms():
    with pytest.raises(ValueError):
        parse_pointer("a/b")
