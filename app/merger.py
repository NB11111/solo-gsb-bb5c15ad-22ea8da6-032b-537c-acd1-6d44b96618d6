"""Three-way JSON merge driven by JSON Pointer paths.

The engine merges ``left`` and ``right`` revisions against their common
``base`` document:

* a change made on only one side is accepted automatically;
* when both sides make the same change it is merged once;
* when one side deletes a value and the other modifies it, or both sides
  change the same value to different results, the base value is kept and a
  conflict is reported (kind ``delete_vs_modify`` / ``both_modified``);
* when a key exists on neither side of the base and the two sides add
  different values, the left value is kept and a ``both_added`` conflict is
  reported.

Object arrays listed in the identity mapping are merged per item keyed by an
identity field. Arrays without an identity field are treated as atomic
values (replaced wholesale, never diffed element by element).

The merge result is deterministic for identical inputs.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any, NamedTuple, Optional

# A document value may be any JSON value; the response model mirrors this.
JsonValue = Optional[Any]

# Sentinel meaning "the key / item is absent on this side".  It is distinct
# from JSON ``null`` during the merge but rendered as ``null`` in responses.
MISSING: Any = object()


class IdMarker(NamedTuple):
    """Pointer step marking an item inside an identity-merged array.

    During merging items are addressed by their identity value instead of a
    positional index, because indices shift as earlier items are deleted.
    Markers are resolved to concrete indices once the final array order is
    known (see :meth:`Merger._resolve_paths`).
    """

    id_key: str
    id_value: Any


class RawConflict(NamedTuple):
    kind: str
    path: tuple
    base: Any
    left: Any
    right: Any


class Merger:
    def __init__(self, identity_map: Optional[dict[str, str]] = None) -> None:
        # Exact-pointer config, keyed by token tuple.
        self._exact: dict[tuple[str, ...], str] = {}
        # Wildcard config: "*" tokens match a single concrete index token.
        self._wild: list[tuple[tuple[str, ...], str]] = []
        for pointer, id_key in (identity_map or {}).items():
            if not isinstance(id_key, str) or not id_key:
                raise ValueError(
                    f"identity field for pointer {pointer!r} must be a non-empty string"
                )
            tokens = parse_pointer(pointer)
            if any(t == "*" for t in tokens):
                self._wild.append((tokens, id_key))
            else:
                self._exact[tokens] = id_key
        # More specific (more literal tokens) patterns first; the rest is a
        # total ordering so lookup is deterministic.
        self._wild.sort(key=lambda p: (-sum(t != "*" for t in p[0]), -len(p[0]), p[0]))

    # ------------------------------------------------------------------ API

    def merge(self, base: Any, left: Any, right: Any) -> tuple[Any, list[RawConflict]]:
        conflicts: list[RawConflict] = []
        merged = self._merge_value(base, left, right, (), conflicts)
        resolved = self._resolve_paths(merged, [c.path for c in conflicts])
        ordered = sorted(
            zip(resolved, conflicts),
            key=lambda pair: (pair[0], pair[1].kind),
        )
        final_conflicts = [
            RawConflict(
                kind=conflict.kind,
                path=path,
                base=_public(conflict.base),
                left=_public(conflict.left),
                right=_public(conflict.right),
            )
            for path, conflict in ordered
        ]
        return merged, final_conflicts

    # --------------------------------------------------------- identity cfg

    def _identity_key(self, logical_path: tuple) -> Optional[str]:
        """Return the configured identity field for the array at *path*.

        Identity arrays are addressed positionally from the document root;
        an item inside an identity array contributes a wildcard ``"*"`` step
        so that ``/servers/*/disks`` matches any parent item.
        """
        # Merge-time logical paths only contain string object keys and
        # IdMarker steps for items of identity arrays; plain arrays never
        # recurse, so positional integer steps never occur here.
        tokens = tuple(
            "*" if isinstance(step, IdMarker) else str(step)
            for step in logical_path
        )
        if tokens in self._exact:
            return self._exact[tokens]
        for pattern, id_key in self._wild:
            if len(pattern) != len(tokens):
                continue
            if all(p == "*" or p == t for p, t in zip(pattern, tokens)):
                return id_key
        return None

    # ------------------------------------------------------------ merging

    def _merge_value(
        self,
        base: Any,
        left: Any,
        right: Any,
        path: tuple,
        conflicts: list[RawConflict],
    ) -> Any:
        # Both absent: the location simply does not exist.
        if left is MISSING and right is MISSING:
            return MISSING

        # One side absent, the other present.
        if left is MISSING:
            if base is MISSING:
                return right  # added on the right only
            if json_equal(right, base):
                return MISSING  # right untouched -> accept left's deletion
            conflicts.append(
                RawConflict("delete_vs_modify", path, base, MISSING, right)
            )
            return base
        if right is MISSING:
            if base is MISSING:
                return left  # added on the left only
            if json_equal(left, base):
                return MISSING  # left untouched -> accept right's deletion
            conflicts.append(
                RawConflict("delete_vs_modify", path, base, left, MISSING)
            )
            return base

        # Both sides present.
        if json_equal(left, right):
            return left  # both made the same change (or neither changed)

        # Sides differ. Structured merges recurse first so independent
        # nested edits are accepted individually and only truly conflicting
        # leaves are reported. This must precede the one-sided shortcuts.
        if isinstance(base, dict) and isinstance(left, dict) and isinstance(right, dict):
            return self._merge_object(left, right, base, path, conflicts)

        if (
            isinstance(base, list)
            and isinstance(left, list)
            and isinstance(right, list)
            and (id_key := self._identity_key(path)) is not None
            and _identity_eligible(base, left, right, id_key)
        ):
            return self._merge_identity_array(
                base, left, right, path, id_key, conflicts
            )

        # Atomic values (and arrays without identity configuration).
        if base is MISSING:
            conflicts.append(
                RawConflict("both_added", path, MISSING, left, right)
            )
            return left
        if json_equal(base, right):
            return left  # changed only on the left
        if json_equal(base, left):
            return right  # changed only on the right

        conflicts.append(RawConflict("both_modified", path, base, left, right))
        return base

    def _merge_object(
        self,
        left: dict,
        right: dict,
        base: dict,
        path: tuple,
        conflicts: list[RawConflict],
    ) -> dict:
        result: dict[str, Any] = {}
        for key in sorted(set(base) | set(left) | set(right)):
            b = base.get(key, MISSING)
            l = left.get(key, MISSING)
            r = right.get(key, MISSING)
            merged = self._merge_value(b, l, r, path + (key,), conflicts)
            if merged is not MISSING:
                result[key] = merged
        return result

    def _merge_identity_array(
        self,
        base: list,
        left: list,
        right: list,
        path: tuple,
        id_key: str,
        conflicts: list[RawConflict],
    ) -> list:
        base_map = _index_items(base, id_key)
        left_map = _index_items(left, id_key)
        right_map = _index_items(right, id_key)

        result: list[Any] = []
        for canon_id, base_item in base_map.items():
            left_item = left_map.get(canon_id, MISSING)
            right_item = right_map.get(canon_id, MISSING)
            marker = IdMarker(id_key, base_item[id_key])

            if left_item is MISSING and right_item is MISSING:
                continue  # deleted on both sides
            if left_item is MISSING:
                if json_equal(right_item, base_item):
                    continue  # right untouched: accept the deletion
                # Deleted on the left, modified on the right: keep base.
                conflicts.append(
                    RawConflict(
                        "delete_vs_modify",
                        path + (marker,),
                        base_item,
                        MISSING,
                        right_item,
                    )
                )
                result.append(base_item)
                continue
            if right_item is MISSING:
                if json_equal(left_item, base_item):
                    continue  # left untouched: accept the deletion
                # Modified on the left, deleted on the right: keep base.
                conflicts.append(
                    RawConflict(
                        "delete_vs_modify",
                        path + (marker,),
                        base_item,
                        left_item,
                        MISSING,
                    )
                )
                result.append(base_item)
                continue

            # Present on both sides.
            if json_equal(left_item, right_item):
                result.append(left_item)
                continue
            if json_equal(base_item, left_item):
                result.append(right_item)
            elif json_equal(base_item, right_item):
                result.append(left_item)
            else:
                # Divergent edits to the same identified item: three-way
                # merge the objects so non-conflicting fields are kept and
                # conflicts point at the item's identity.
                merged_item = self._merge_object(
                    left_item,
                    right_item,
                    base_item,
                    path + (marker,),
                    conflicts,
                )
                result.append(merged_item)

        # Items present in base order were emitted first. Append items that
        # are new on one side (or both), sorted by identity value.
        new_ids = (set(left_map) | set(right_map)) - set(base_map)
        new_items: list[tuple[Any, Any]] = []
        for canon_id in new_ids:
            left_item = left_map.get(canon_id, MISSING)
            right_item = right_map.get(canon_id, MISSING)
            if left_item is MISSING:
                new_items.append((right_item[id_key], right_item))
            elif right_item is MISSING:
                new_items.append((left_item[id_key], left_item))
            elif json_equal(left_item, right_item):
                new_items.append((left_item[id_key], left_item))
            else:
                # Both sides add the same new identity with different content.
                # The merged document contains the left value; the conflict
                # is reported on the parent array (the item has no stable
                # positional pointer before sorting).
                conflicts.append(
                    RawConflict("both_added", path, MISSING, left_item, right_item)
                )
                new_items.append((left_item[id_key], left_item))
        new_items.sort(key=lambda pair: _sort_key(pair[0]))
        result.extend(item for _, item in new_items)
        return result

    # ---------------------------------------------------- path resolution

    def _resolve_paths(self, merged: Any, paths: list[tuple]) -> list[str]:
        """Translate internal paths (with IdMarkers) into RFC 6901 pointers.

        Marker steps are replaced by the final index of the identified item
        inside the merged array. Every marker references a base item that was
        kept on conflict, so it is always resolvable in ``merged``.
        """
        # Map identity-array path -> {identity value: final index}.
        id_index: dict[tuple, dict[Any, int]] = {}

        def walk(node: Any, path: tuple) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, path + (key,))
            elif isinstance(node, list):
                id_keys: set[str] = set()
                for conflict_path in paths:
                    if (
                        conflict_path[: len(path)] == path
                        and len(conflict_path) > len(path)
                        and isinstance(conflict_path[len(path)], IdMarker)
                    ):
                        id_keys.add(conflict_path[len(path)].id_key)
                if id_keys:
                    (id_key,) = id_keys
                    id_index[path] = {
                        item[id_key]: i
                        for i, item in enumerate(node)
                        if isinstance(item, dict) and id_key in item
                    }
                for i, item in enumerate(node):
                    walk(item, path + (i,))

        walk(merged, ())

        rendered: list[str] = []
        for path in paths:
            steps: list[str] = []
            cur: tuple = ()
            for step in path:
                if isinstance(step, IdMarker):
                    idx = id_index[cur][step.id_value]
                    steps.append(escape_token(str(idx)))
                    cur = cur + (idx,)
                elif isinstance(step, int):
                    steps.append(escape_token(str(step)))
                    cur = cur + (step,)
                else:
                    steps.append(escape_token(str(step)))
                    cur = cur + (step,)
            rendered.append("" if not steps else "/" + "/".join(steps))
        return rendered


# ---------------------------------------------------------------- helpers


def parse_pointer(pointer: str) -> tuple[str, ...]:
    """Parse a JSON Pointer (RFC 6901) into reference tokens.

    ``""`` denotes the whole document. A leading ``"#"`` (URI fragment form)
    and fragment percent-decoding are intentionally not supported.
    """
    if not isinstance(pointer, str):
        raise ValueError(f"JSON Pointer must be a string, got {type(pointer).__name__}")
    if pointer == "":
        return ()
    if not pointer.startswith("/"):
        raise ValueError(
            f"invalid JSON Pointer {pointer!r}: non-empty pointers must start with '/'"
        )
    tokens: list[str] = []
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        tokens.append(token)
    return tuple(tokens)


def escape_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def json_equal(a: Any, b: Any) -> bool:
    """Structural equality with JSON semantics.

    Unlike Python's ``==``, booleans never compare equal to numbers, and the
    ``MISSING`` sentinel is only equal to itself.
    """
    if a is MISSING or b is MISSING:
        return a is b
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, str) or isinstance(b, str):
        return isinstance(a, str) and isinstance(b, str) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False
        return all(json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    return a == b


def _identity_eligible(base: list, left: list, right: list, id_key: str) -> bool:
    """Identity merge requires every item (on every side) to be an object
    carrying the identity field, with no duplicated identity per side."""
    for side in (base, left, right):
        seen: set[str] = set()
        for item in side:
            if not isinstance(item, dict) or id_key not in item:
                return False
            canon = _canonical_id(item[id_key])
            if canon in seen:
                return False
            seen.add(canon)
    return True


def _canonical_id(value: Any) -> str:
    # sort_keys keeps {"a":1,"b":2} and {"b":2,"a":1} equivalent; such a
    # composite identity is unusual but the ordering must be total.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _index_items(items: list, id_key: str) -> "OrderedDict[str, dict]":
    indexed: "OrderedDict[str, dict]" = OrderedDict()
    for item in items:
        indexed[_canonical_id(item[id_key])] = item
    return indexed


def _sort_key(value: Any):
    """Total ordering over JSON scalar identity values.

    Ordering: null < false < true < numbers < strings. Numbers sort by their
    numeric value. Strings sort lexicographically.
    """
    if value is None:
        return (0,)
    if isinstance(value, bool):
        return (1, int(value))
    if isinstance(value, (int, float)):
        return (2, float(value))
    if isinstance(value, str):
        return (3, value)
    # Composite (array/object) identity: fall back to canonical JSON text.
    return (4, _canonical_id(value))


def _public(value: Any) -> Any:
    """Render an internal value for the API response (MISSING -> null)."""
    return None if value is MISSING else value
