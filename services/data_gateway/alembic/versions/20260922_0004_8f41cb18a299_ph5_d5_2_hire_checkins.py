"""The 90-day hire check-in — PH5-D5-2 (C1 quality-of-hire).

Revision ID: 8f41cb18a299
Revises: c8e0a2b4d6f8
Create Date: 2026-09-22

WHAT THIS IS
"Hired" has never meant anything past the offer. This adds ``hire_checkins``:
one HR-recorded outcome per hire, at roughly 90 days after the job actually
started — still employed or not, and if still employed, a coarse performance
read. It is a SIGNAL for PH5-C1's quality-of-hire view (retention, performance
mix, by channel), never a decision: nothing here can move
``enrolments.status``, and the trigger below refuses to let it try.

NO FREE TEXT, NO ``applicant_id`` (DPDP review)
The row is exactly ``employment`` (``employed``/``left``), ``left_reason``
(iff ``left``) and ``performance`` (iff ``employed``) — no note, and
therefore nothing on this row that a person wrote. There is also no
``applicant_id``: ``requisitions.merge_applicants`` repoints every table keyed
by ``applicant_id`` directly (``enrolments``, ``exam_assignments``,
``exam_attempts``, ``interview_invites``) to the surviving applicant when two
duplicate applicant rows are merged, and this table's freeze trigger would
refuse that UPDATE outright — it is not the supersede transition, so it would
be indistinguishable from tampering. The person is reached through
``enrolment_id -> enrolments.applicant_id``, which ``merge_applicants``
already repoints correctly today (the same choice ``interviewer_scorecards``
made).

RETENTION AND ERASURE BOTH DELETE THE ROW OUTRIGHT (DPDP review)
With no note and no applicant reference, this row would otherwise be the
``round_results``/``enrolments`` precedent (a structural record kept forever
against an anonymised applicant) — except the DPDP review decided differently
for this table specifically: it is deleted outright, both by the ordinary
24-month retention sweep (``app.hire_checkins.purge``) and, sooner, by a new
erasure-executor step when the applicant is erased. So this table, unlike
``interviewer_scorecards`` or ``sessions``/``scorecards``, is not "kept
forever until policy says otherwise" — deletion is its designed end state.
The composite FK to ``enrolments`` is CASCADE for the same reason a
hard-deleted company or requisition must not be blocked by a check-in row
(the composite-SET-NULL hazard this phase has hit three times does not apply
to CASCADE, which deletes the row rather than nulling it).

DELETE IS UNRESTRICTED — A REAL GAP, MITIGATED BY A TEST, NOT BY THE DATABASE
(security review LOW-4). The trigger below places no restriction on DELETE at
all, which means a caller who deleted a live check-in and immediately
recorded a fresh one would, in effect, overwrite it: the old value is gone,
no ``supersedes_id`` links the new row to it, and none of the "corrections
supersede" guarantee below applied. That is a real hole in the audit trail,
not a false alarm, and it is not recorded in ``docs/ACCEPTED-RISKS.md`` — it
is closed by convention plus a test, not by a control that holds regardless
of who is asking. Only two callers ever issue a DELETE against this table —
``app.hire_checkins.purge`` (retention) and the erasure executor's step 5j —
and ``tests/unit/test_ph5_w1_checkins.py::test_delete_from_hire_checkins_has_exactly_two_callers``
greps all of ``services/`` and fails if a third one is ever added, HR-facing
route included. That is enforcement at review time, not at the database:
distinguishing "the retention job" from "a rogue direct DELETE" at the
trigger would need a marker the caller sets on its own connection (a session
GUC, an application_name), and every one of those is only ADVISORY — the
retention job and any other caller connect as the same database role, so
nothing stops that caller from setting the same marker itself. A structural
fix needs a DIFFERENT role for the two write paths (a Tier-2 migration, not
this one) or accepting the ``interviewer_scorecards`` shape of refusing
DELETE outright, which both legitimate callers here need NOT to have: each
must delete a live row's whole chain in one statement, including rows a
flat refusal would block.

"THE HIRE STANDS" (DPDP review)
A hire that was later reversed — the offer declined, expired or withdrawn
after ``enrolments.status`` reached ``hired`` — is not a hire to check in on.
The RULE is shared byte-for-byte as ``HIRE_STANDS_SQL`` across this
migration, ``app.hire_checkins`` and PH5-C2's metric layer's own ``hired``
flag:

    e.status = 'hired' AND COALESCE(e.offer_outcome, '') NOT IN
    ('offer_declined', 'offer_expired', 'offer_withdrawn')

— aliased to ``e`` on purpose, so the same text drops into any query that
also joins ``applicants`` (its own ``status`` column) with no ambiguity. The
TRIGGER below is not this string: PL/pgSQL spells the same rule as
``<> ALL (ARRAY[...])`` over ``REVERSED_OFFER_OUTCOMES``'s three literals
rather than ``NOT IN (...)``. A unit test checks the trigger's own three
literals against the other copies, not a string match, since a string match
would be comparing two different syntaxes for one rule.

CORRECTIONS SUPERSEDE, THEY DO NOT OVERWRITE
HR recording the wrong thing (wrong employment status) opens a correction: a
NEW row is inserted carrying ``supersedes_id``, and the OLD row is stamped
``superseded_at`` — never edited into a new shape. Both stay readable. There
is no correction reason column (security review LOW-2, removed from the
original design): collecting one had no stated purpose it served, and it was
one refactor away from becoming free text in an audit log that is never
redacted (AR-5). ``audit_log`` records only which row a correction supersedes
— an id, never a value.

THE SUPERSEDE CHAIN CAN ONLY EVER BE ONE HIRE'S OWN (security review
MEDIUM-3). The original shape let ``supersedes_id`` name any row in the same
company, so a bug (or a crafted request) that mixed up two enrolments would
have superseded a DIFFERENT hire's check-in — and the erasure executor's
whole-chain DELETE (step 5j, admin_ops) would then fail forever with a
foreign-key violation the moment it tried to delete one enrolment's rows
while the other enrolment's row still pointed into the deleted set. Fixed
structurally: the self-referential foreign key is now
``(supersedes_id, enrolment_id, company_id) -> hire_checkins (id,
enrolment_id, company_id)``, so a correction's target must already belong to
the SAME enrolment — not merely the same company — or the INSERT is refused
by the database, and ``uq_hire_checkins_supersedes`` gives each row at most
one successor. The trigger adds the two checks a foreign key cannot express:
the target must already be superseded (so a correction never creates a
second live successor of one row) and of the same ``kind``.

THE TRIGGER
``hire_checkins_lifecycle()``:

* DELETE — always allowed. Retention (``app.hire_checkins.purge``) and the
  erasure executor's own step both delete rows outright on purpose, and a
  hard-deleted enrolment or company cascades here too. See "DELETE IS
  UNRESTRICTED" above for the gap this leaves and how it is actually
  mitigated (a test, not a database control).
* INSERT — refused unless the target enrolment's hire stands (see above) AT
  THAT MOMENT. The service checks this too, for a clean 409; the trigger is
  the guarantee that holds even if a future caller forgets. When
  ``supersedes_id`` is set, the target row must already be superseded and of
  the same ``kind`` — see "THE SUPERSEDE CHAIN" above.
* UPDATE — every column is frozen except ``superseded_at``, which may move
  from NULL to set exactly once and alone in the statement. Anything else —
  a changed employment/performance value, an un-supersession — is refused
  with an exception naming the row.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "8f41cb18a299"
down_revision: str | None = "c8e0a2b4d6f8"
branch_labels: str | None = None
depends_on: str | None = None

KINDS = ("90_day",)
EMPLOYMENT = ("employed", "left")
LEFT_REASONS = ("voluntary", "involuntary", "unknown")
PERFORMANCE = ("below", "meets", "exceeds")

#: The three offer outcomes that mean a recorded hire did not stand. Mirrors
#: ``app.hire_checkins.REVERSED_OFFER_OUTCOMES`` and
#: ``app.metrics.definitions.REVERSED_OFFER_OUTCOMES`` — a unit test pins all
#: three copies together.
REVERSED_OFFER_OUTCOMES = ("offer_declined", "offer_expired", "offer_withdrawn")

#: Documents the rule this migration enforces; byte-identical to
#: ``app.hire_checkins.HIRE_STANDS_SQL`` and
#: ``app.metrics.definitions.HIRE_STANDS_SQL``. NOT what the trigger itself
#: contains — see below.
HIRE_STANDS_SQL = (
    "e.status = 'hired' AND COALESCE(e.offer_outcome, '') NOT IN "
    "('offer_declined', 'offer_expired', 'offer_withdrawn')"
)

HIRE_CHECKINS_LIFECYCLE = f"""
CREATE OR REPLACE FUNCTION hire_checkins_lifecycle() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;  -- retention, erasure and cascade all delete outright on purpose
    END IF;

    IF TG_OP = 'INSERT' THEN
        -- Same RULE as HIRE_STANDS_SQL above, spelled the way PL/pgSQL wants
        -- it -- an ALL-quantified array comparison over the same three
        -- REVERSED_OFFER_OUTCOMES literals rather than a NOT IN list --
        -- different syntax, one rule.
        IF NOT EXISTS (
            SELECT 1 FROM enrolments e
             WHERE e.id = NEW.enrolment_id AND e.company_id = NEW.company_id
               AND e.status = 'hired'
               AND COALESCE(e.offer_outcome, '') <> ALL (ARRAY{list(REVERSED_OFFER_OUTCOMES)!r})
        ) THEN
            RAISE EXCEPTION 'a hire check-in needs a hire that stands (enrolment %)',
                NEW.enrolment_id;
        END IF;
        IF NEW.superseded_at IS NOT NULL THEN
            RAISE EXCEPTION 'a hire check-in arrives not superseded';
        END IF;
        -- The supersede chain can only ever be one hire's own (security
        -- review MEDIUM-3). The foreign key already requires the target to
        -- share this row's (enrolment_id, company_id); this adds the two
        -- checks a foreign key cannot express.
        IF NEW.supersedes_id IS NOT NULL THEN
            IF NOT EXISTS (
                SELECT 1 FROM hire_checkins t
                 WHERE t.id = NEW.supersedes_id
                   AND t.enrolment_id = NEW.enrolment_id
                   AND t.company_id = NEW.company_id
                   AND t.superseded_at IS NOT NULL
                   AND t.kind = NEW.kind
            ) THEN
                RAISE EXCEPTION
                    'hire check-in %: supersedes_id must name an already-superseded'
                    ' check-in of the same kind for the same hire', NEW.id;
            END IF;
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE: superseded_at may move from NULL to set, once, alone.
    IF NEW.superseded_at IS DISTINCT FROM OLD.superseded_at THEN
        IF OLD.superseded_at IS NOT NULL THEN
            RAISE EXCEPTION 'hire check-in % is already superseded', OLD.id;
        END IF;
        IF NEW.superseded_at IS NULL THEN
            RAISE EXCEPTION 'a hire check-in is never un-superseded';
        END IF;
        IF (to_jsonb(NEW) - 'superseded_at' - 'updated_at')
           IS DISTINCT FROM (to_jsonb(OLD) - 'superseded_at' - 'updated_at') THEN
            RAISE EXCEPTION 'superseding a hire check-in changes only superseded_at';
        END IF;
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'hire check-in % is fixed; correct it instead of editing it', OLD.id;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "hire_checkins",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("enrolment_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="90_day"),
        sa.Column("employment", sa.Text(), nullable=False),
        sa.Column("left_reason", sa.Text(), nullable=True),
        sa.Column("performance", sa.Text(), nullable=True),
        sa.Column("recorded_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "recorded_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hire_checkins"),
        sa.UniqueConstraint("id", "company_id", name="uq_hire_checkins_id_company"),
        # Lets the self-referential FK below require a correction's target to
        # share ITS enrolment, not merely its company (security review
        # MEDIUM-3).
        sa.UniqueConstraint(
            "id", "enrolment_id", "company_id", name="uq_hire_checkins_id_enrolment_company",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_hire_checkins_company",
            ondelete="CASCADE",
        ),
        # CASCADE, not RESTRICT: see the module docstring — deletion (retention,
        # erasure, or a hard-deleted parent) is this table's designed end state,
        # not an edge case to guard against.
        sa.ForeignKeyConstraint(
            ["enrolment_id", "company_id"], ["enrolments.id", "enrolments.company_id"],
            name="fk_hire_checkins_enrolment", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"], ["users.id"], name="fk_hire_checkins_recorded_by",
        ),
        # (supersedes_id, enrolment_id, company_id), not just (supersedes_id,
        # company_id): a correction can only ever point at an earlier row of
        # its OWN hire. Without enrolment_id in the key, a bug that mixed up
        # two enrolments in the same company would have gone unrefused, and
        # the erasure executor's whole-chain DELETE (step 5j) would then fail
        # forever with a foreign-key violation the moment it deleted one
        # enrolment's rows while the other's row still referenced them
        # (security review MEDIUM-3).
        sa.ForeignKeyConstraint(
            ["supersedes_id", "enrolment_id", "company_id"],
            ["hire_checkins.id", "hire_checkins.enrolment_id", "hire_checkins.company_id"],
            name="fk_hire_checkins_supersedes",
        ),
        # A plain tuple->str of a ONE-element tuple leaves a trailing comma
        # before the paren ("IN ('90_day',)"), which Postgres refuses outright
        # -- confirmed against a real instance while writing this migration.
        # `= ANY (...)` sidesteps the whole single/multi-element formatting
        # question, and reads the same whichever way KINDS grows.
        sa.CheckConstraint(
            f"kind = ANY (ARRAY{list(KINDS)!r})", name="ck_hire_checkins_kind",
        ),
        sa.CheckConstraint(f"employment IN {EMPLOYMENT}", name="ck_hire_checkins_employment"),
        sa.CheckConstraint(
            f"left_reason IS NULL OR left_reason IN {LEFT_REASONS}",
            name="ck_hire_checkins_left_reason_vocab",
        ),
        sa.CheckConstraint(
            f"performance IS NULL OR performance IN {PERFORMANCE}",
            name="ck_hire_checkins_performance_vocab",
        ),
        # iff rules: left_reason exactly when left; performance exactly when
        # employed (never both, never neither).
        sa.CheckConstraint(
            "(employment = 'left') = (left_reason IS NOT NULL)",
            name="ck_hire_checkins_left_reason_iff",
        ),
        sa.CheckConstraint(
            "(employment = 'employed') = (performance IS NOT NULL)",
            name="ck_hire_checkins_performance_iff",
        ),
    )
    # One LIVE check-in per (enrolment, kind) — the key relationship. Partial,
    # so a superseded row never blocks its correction.
    op.create_index(
        "uq_hire_checkins_live", "hire_checkins", ["enrolment_id", "kind"],
        unique=True, postgresql_where=sa.text("superseded_at IS NULL"),
    )
    # One successor per row (security review MEDIUM-3) — also what makes the
    # composite self-FK's target side an indexable unique key.
    op.create_index(
        "uq_hire_checkins_supersedes", "hire_checkins", ["supersedes_id"],
        unique=True, postgresql_where=sa.text("supersedes_id IS NOT NULL"),
    )
    op.create_index(
        "ix_hire_checkins_enrolment", "hire_checkins", ["company_id", "enrolment_id"],
    )
    # app.hire_checkins.purge's retention sweep.
    op.create_index("ix_hire_checkins_recorded_at", "hire_checkins", ["recorded_at"])

    op.execute(HIRE_CHECKINS_LIFECYCLE)
    op.execute(
        "CREATE TRIGGER hire_checkins_lifecycle"
        " BEFORE INSERT OR UPDATE OR DELETE ON hire_checkins"
        " FOR EACH ROW EXECUTE FUNCTION hire_checkins_lifecycle()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS hire_checkins_lifecycle ON hire_checkins")
    op.execute("DROP FUNCTION IF EXISTS hire_checkins_lifecycle()")
    op.drop_index("ix_hire_checkins_recorded_at", table_name="hire_checkins")
    op.drop_index("ix_hire_checkins_enrolment", table_name="hire_checkins")
    op.drop_index("uq_hire_checkins_supersedes", table_name="hire_checkins")
    op.drop_index("uq_hire_checkins_live", table_name="hire_checkins")
    op.drop_table("hire_checkins")
