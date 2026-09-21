"""Request/response models for the merge API."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

# Any JSON document: object, array, string, number, boolean or null.
JsonValue = Optional[Any]


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    base: JsonValue = Field(
        ..., description="Common ancestor of the two revisions."
    )
    left: JsonValue = Field(..., description="The left-hand revision.")
    right: JsonValue = Field(..., description="The right-hand revision.")
    array_identity: dict[str, str] = Field(
        default_factory=dict,
        alias="arrayIdentity",
        description=(
            "Mapping from a JSON Pointer (RFC 6901) of an object array to the "
            "name of the item field that uniquely identifies an entry. The "
            "token '*' matches a single positional index, e.g. "
            "'/servers/*/disks' -> 'name'."
        ),
    )


class Conflict(BaseModel):
    pointer: str = Field(..., description="JSON Pointer (RFC 6901) of the conflict.")
    kind: str = Field(
        ...,
        description=(
            "both_modified: both sides changed the value differently; "
            "delete_vs_modify: one side deleted what the other modified; "
            "both_added: both sides added different values at a path absent "
            "from base."
        ),
    )
    base: JsonValue
    left: JsonValue
    right: JsonValue


class MergeResponse(BaseModel):
    merged: JsonValue
    conflicts: list[Conflict]
