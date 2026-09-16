"""The one definition of "this opening is live to the public" — PH3-B0.

The problem this solves
-----------------------
"Is this opening visible to the open web?" was answered independently in three
SQL queries and read off a single column in four more display surfaces, and the
answers had already drifted. ``routers/public_apply`` refused an opening with no
published workflow; ``routers/careers`` and ``routers/candidate_applications``
did not — so the board advertised roles that 404'd on click, which is the one
failure a job board really cannot have. The existing cross-check test compared
three of the gates and so never saw it.

PH3 makes that drift much more expensive. PH3-B2 has since added an approval
gate and PH3-B4a a scheduled-publish gate to this same predicate, and the
acceptance criterion each of them had to meet — "an unapproved requisition
cannot accidentally become publicly available" — is precisely the one that
fails on the surface somebody forgot to edit. Both were one edit to the tuple
below.

So the predicate lives here once, as data, and
``tests/unit/test_ph3_publish_gate.py`` fails the build if another module starts
spelling it out again. Adding a gate is then one edit in one tuple.

The two halves
--------------
``visible_sql()`` is the SQL, for queries that select openings.
``public_gate_open()`` and ``is_publicly_live()`` are the same rules in Python,
for surfaces that have already loaded a row and need to report on it.

They are deliberately split at the workflow gate, because HR's board needs the
two facts apart: an opening that is switched on but has no published workflow is
*accepting applications it cannot process*, and saying so is the whole point of
``company_board``'s ``not_published`` band. Collapsing them into one boolean
would delete that message. The public surfaces want them collapsed, which is
what ``is_publicly_live`` does.
"""

from __future__ import annotations

from datetime import UTC, datetime

#: The one approval state that lets an opening reach the public (PH3-B2). The
#: full lifecycle lives in ``app.requisition_approval``; this module only needs
#: to know which end of it opens the gate.
APPROVED = "approved"

# Every gate an opening must pass before the open web may see or apply to it.
#
# Ordered cheapest-first, which is also roughly most-selective-first: the
# partial indexes ``ix_job_requisitions_public_open`` and
# ``ix_job_requisitions_board`` are built on the first three, so a query that
# leads with them can use either.
#
# ``{a}`` is the job_requisitions alias. ``:now`` is bound by the caller.
#
# TO ADD A GATE (PH3-B2 approval, PH3-B4a scheduled publish): add the row here
# and add the Python mirror below. Do not add it to a query.
_REQUISITION_GATES: tuple[tuple[str, str], ...] = (
    ("not_deleted", "{a}.deleted_at IS NULL"),
    ("status_open", "{a}.status = 'open'"),
    ("public_apply_enabled", "{a}.public_apply_enabled"),
    # PH3-B2. Added here and nowhere else, which is the whole reason this
    # module exists: three public surfaces and four HR-facing ones picked this
    # up from one edit. Requisitions that predate the approval gate were
    # grandfathered as approved by the migration, so nothing went dark.
    ("approved", "{a}.approval_status = 'approved'"),
    # A closing date that has passed stops applications without HR having to
    # remember to flip the status.
    ("not_past_closing_date", "({a}.closes_at IS NULL OR {a}.closes_at > :now)"),
)

# Separate because it is not a property of the requisition row. An opening
# switched on before its process existed took candidates into nothing: no
# scoring gate, no first round, nobody told (E4).
_PUBLISHED_WORKFLOW_GATE = (
    "EXISTS (SELECT 1 FROM workflows w"
    " WHERE w.requisition_id = {a}.id"
    "   AND w.company_id = {a}.company_id"
    "   AND w.status = 'published'"
    "   AND w.deleted_at IS NULL)"
)

# Named so tests and reviewers can enumerate what the gate consists of without
# parsing SQL.
GATE_NAMES: tuple[str, ...] = (
    *(name for name, _ in _REQUISITION_GATES),
    "has_published_workflow",
)


def visible_sql(alias: str = "r", *, require_published_workflow: bool = True) -> str:
    """The public-visibility predicate, as a SQL boolean expression.

    Returned without a leading ``AND`` so the caller decides whether it opens a
    ``WHERE`` or extends one. The caller must bind ``:now``.

    ``require_published_workflow=False`` drops only the workflow gate, for the
    one caller that needs the requisition's own flags on their own. It does not
    exist to make the predicate optional.
    """
    parts = [sql.format(a=alias) for _, sql in _REQUISITION_GATES]
    if require_published_workflow:
        parts.append(_PUBLISHED_WORKFLOW_GATE.format(a=alias))
    return "\n   AND ".join(parts)


def public_gate_open(
    *,
    status: str | None,
    public_apply_enabled: bool | None,
    approval_status: str | None,
    closes_at: datetime | None = None,
    deleted_at: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """The requisition's own flags say the open web may apply.

    Everything in :data:`_REQUISITION_GATES`, and nothing about whether a
    workflow exists to process the application. For HR-facing surfaces, which
    report "accepting applications" and "has a published workflow" as two
    separate facts.

    ``approval_status`` is REQUIRED, deliberately. It defaulted to approved so
    that a caller which had not yet been taught about PH3-B2 kept working — a
    fail-open default in the one module that exists to decide what the public
    may see. Nothing was exposed by it (the public surfaces use ``visible_sql``,
    which has no such escape), but a required argument means the next caller has
    to say what it means rather than inherit an answer.
    """
    if deleted_at is not None:
        return False
    if status != "open":
        return False
    if not public_apply_enabled:
        return False
    if approval_status != APPROVED:
        return False
    return not (closes_at is not None and closes_at <= (now or datetime.now(tz=UTC)))


def is_publicly_live(
    *,
    status: str | None,
    public_apply_enabled: bool | None,
    has_published_workflow: bool,
    approval_status: str | None,
    closes_at: datetime | None = None,
    deleted_at: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """Every gate, including the published workflow. The Python mirror of
    ``visible_sql()`` — what a public surface would have shown."""
    return has_published_workflow and public_gate_open(
        status=status,
        public_apply_enabled=public_apply_enabled,
        approval_status=approval_status,
        closes_at=closes_at,
        deleted_at=deleted_at,
        now=now,
    )
