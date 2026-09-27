"""Pydantic models for talent pools — PH5-E3.

The route contract (``ph5_e3_contracts.md`` §2) is fixed and the frontend is
built against it directly, so every field name and nesting here is the
contract, not a convenience shape. Unlike the rediscovery search response,
these routes do NOT use ``response_model_exclude_none=True`` — every field is
present with ``null`` where the contract shows ``"…|null"``, matching the
convention the contract uses everywhere in this section.

WHAT IS NOT IN HERE, deliberately, on the ``app/schemas/rediscovery.py``
precedent:
  * no hiring field. There is no ``status``, ``stage``, ``decision`` or
    ``recommendation`` anywhere below, and a pool member has no way to
    express one. The invite route creates an enrolment and nothing else.
  * no ``why_sentence`` — ``match_reason`` is a FROZEN snapshot of facts, never
    a sentence a model wrote.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Source = Literal["manual", "rediscovery"]
IneligibleReason = Literal["consent_withdrawn", "consent_expired", "erasure_requested"]
Freshness = Literal["fresh", "ageing", "stale", "unverifiable", "none"]

#: Second gate on ``MemberAddIn.match_reason``, ahead of
#: ``app.talent_pools._sanitise_match_reason`` — that function re-applies the
#: field allowlist and its own per-item/per-term bounds (``_MAX_MATCH_REASON_WHY``,
#: ``_MAX_QUERY_TERMS``, ``_MAX_TERM_CHARS``) regardless of what arrived, but
#: only AFTER Pydantic has already parsed an untyped ``dict[str, Any]`` and held
#: the whole thing in memory — an arbitrarily large or deeply nested body is
#: fully validated and constructed before anything ever walks it looking for
#: fields to keep. This bounds the SERIALISED size at the door instead, so a
#: hand-crafted body is rejected with a 422 rather than accepted and processed.
#: A real frozen snapshot (``rediscovery.freeze_match_reason``, itself bounded)
#: is a few KB at most; this is generous headroom for that, not a target size.
_MAX_MATCH_REASON_BYTES = 16 * 1024


# ---------------------------------------------------------------------------
# Pools
# ---------------------------------------------------------------------------
class PoolCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class PoolUpdateIn(BaseModel):
    """Every field optional: a PATCH may rename, re-describe, archive or
    restore a pool in any combination, in one call."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    archived: bool | None = None


class PoolOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    member_count: int
    created_by_name: str | None = None
    created_at: str
    updated_at: str
    archived_at: str | None = None


class PoolLimits(BaseModel):
    max_per_company: int
    max_members: int


class PoolListOut(BaseModel):
    pools: list[PoolOut]
    limits: PoolLimits


class PoolDeleteOut(BaseModel):
    deleted: bool
    members_removed: int


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------
class MemberOut(BaseModel):
    member_id: str
    applicant_id: str
    full_name: str
    current_title: str | None = None
    current_company: str | None = None
    source: Source
    added_at: str
    added_by_name: str | None = None
    note: str | None = None
    #: The frozen "why matched" snapshot as at add time, prose stripped
    #: (design §6.4) — a passthrough dict rather than a nested model, since
    #: its shape is whatever ``rediscovery.freeze_match_reason`` produced and
    #: this schema's job is presence/absence, not re-validating facts that
    #: were already validated on the way OUT of the search endpoint.
    match_reason: dict[str, Any] | None = None
    evidence_freshness: Freshness | None = None
    evidence_reviewed_at: str | None = None
    evidence_reviewed_by_name: str | None = None
    eligible: bool
    ineligible_reason: IneligibleReason | None = None
    opted_in_at: str | None = None
    expires_at: str | None = None
    #: The newest enrolment for this applicant, when one exists — what "Review
    #: evidence" needs for ``/hr/enrolments/{id}/evidence``. ``None`` (never
    #: omitted: this router does not use ``exclude_none``) when there is none.
    enrolment_id: str | None = None


class PoolWithMembersOut(BaseModel):
    pool: PoolOut
    members: list[MemberOut]


class MemberAddIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant_ids: list[str] = Field(min_length=1, max_length=100)
    source: Source
    note: str | None = Field(default=None, max_length=500)
    #: One frozen snapshot applied to every id in this call — the contract's
    #: shape. A caller adding several results with genuinely different
    #: reasons calls this once per applicant, each with its own snapshot and
    #: a single-element ``applicant_ids``.
    match_reason: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _bound_match_reason_size(self) -> MemberAddIn:
        """Reject an oversized or unserialisable ``match_reason`` before it is
        ever walked — see ``_MAX_MATCH_REASON_BYTES`` above. Keeps the existing
        field-level sanitisation in ``app.talent_pools``; this is a second gate,
        not a replacement for it."""
        if self.match_reason is None:
            return self
        try:
            serialised = json.dumps(self.match_reason, default=str)
        except (TypeError, ValueError) as exc:
            raise ValueError("match_reason must be a JSON-serialisable object.") from exc
        size = len(serialised.encode("utf-8"))
        if size > _MAX_MATCH_REASON_BYTES:
            raise ValueError(
                f"match_reason is too large ({size} bytes; max {_MAX_MATCH_REASON_BYTES})."
            )
        return self


class MemberSkip(BaseModel):
    applicant_id: str
    reason: Literal["already_a_member", "not_this_company", "pool_full", "not_eligible"]


class MemberAddOut(BaseModel):
    added: list[str]
    skipped: list[MemberSkip]


class MemberRemoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=200)


class MemberRemoveOut(BaseModel):
    removed: bool


class MemberInviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requisition_id: str
    #: Criterion 14's control (design §6.6 point 3): when this member's
    #: evidence is not fresh AND nobody has marked it reviewed, the invite
    #: refuses with 422 ``stale_evidence_unreviewed`` unless this is true. The
    #: frontend sends it only after an explicit confirmation the candidate
    #: has not just skipped past — an override that leaves no trace is not a
    #: control, so a true value here is recorded on the ``member_invited``
    #: event, facts only.
    acknowledged_stale: bool = False


class MemberInviteOut(BaseModel):
    enrolment_id: str
    requisition_id: str
    already_enrolled: bool
