"""Code quality + similarity evidence — PH4-D3.

Revision ID: f4c6e8a0b2d7
Revises: e3b5d7f9a1c5
Create Date: 2026-09-22

WHAT THIS ADDS
Static, non-executing analysis of a candidate's coding-round submission —
per-function complexity/smells for Python (``ast``), token-level
approximations for the other nine ``SUPPORTED_LANGUAGES`` (Pygments lexers),
and cross-submission similarity via winnowing (the MOSS approach). Four new
tables:

  * ``code_quality_reports``  — one row per (attempt, coding question,
    analyser version). Immutable once written (UPDATE refused); DELETE stays
    legal for retention and erasure.
  * ``code_fingerprints``     — the winnowed k-gram hashes for one submission,
    GIN-indexed for the ``&&`` overlap query the sweep uses to find candidate
    pairs. Same immutability.
  * ``code_similarity_signals`` — SYSTEM evidence: a pair of submissions (or a
    submission against the question's reference solution) whose fingerprints
    overlap enough to be worth a look. Composite FKs from BOTH sides of the
    pair to ``exam_attempts(id, company_id)`` make a cross-tenant pair
    unrepresentable, not merely filtered. Same immutability.
  * ``code_integrity_findings`` — HUMAN evidence: a named person's judgement
    call on a signal (or on the submission generally), with a mandatory
    rationale. This is the ONLY one of the four a person writes to, and it can
    change only by a controlled redaction or a one-time supersede — never a
    verdict rewrite.

THE LOAD-BEARING INVARIANT THIS MIGRATION ENFORCES
A signal is never a finding. Nothing in this migration gives any of the three
system-evidence tables a column that could express or drive a hiring
decision, and none of their triggers permit turning one into a decision.
``code_integrity_findings.outcome`` is a human's own conclusion
(``no_concern`` / ``follow_up`` / ``confirmed``) recorded against a mandatory,
non-empty rationale — it is not read by ``workflow_runner`` or
``record_result``, and nothing here writes to ``exam_attempts`` or
``enrolments`` beyond the one redaction exception below.

WHY A SUBMITTED ATTEMPT'S GRADED RESULT IS NOW FROZEN AT THE DATABASE
``exam_attempts_submission_frozen`` freezes ``answers``, ``graded_snapshot``,
every ``score_*`` column, ``passed``, ``started_at`` and ``submitted_at`` once
``status`` is ``submitted`` or ``expired`` — the same columns
``exam_take._grade_and_finalize`` writes exactly once today, so this changes
no legitimate behaviour. It closes one real gap: a racing second submit that
reached the persist step twice (Redis claim lost, or failed open) used to
silently overwrite an already-graded result; now it errors, and the caller's
own re-fetch-and-return-stored-result path (already in ``exam_take.py``)
handles that before it ever reaches a second UPDATE. The ONE exception is a
redaction: ``answers``/``graded_snapshot`` may change in the SAME statement
that sets ``code_redacted_at`` from NULL, and only those three columns — used
by retention (``app/code_evidence.py::purge``) and DPDP erasure (admin_ops
step 5h) to strip candidate SOURCE and program stdout/stderr while keeping
the score, exactly like every other redaction trigger in this schema.

TENANT ISOLATION BY CONSTRUCTION
Every new table carries composite FKs — ``(attempt_id, company_id)`` and
``(coding_question_id, company_id)`` — to the existing ``(id, company_id)``
unique keys, which this migration adds to ``exam_attempts`` and
``coding_questions`` (neither had one). A cross-tenant pair in
``code_similarity_signals`` is therefore unrepresentable: both attempt FKs
resolve through the SAME ``company_id`` column on that row, so two attempts
from different companies cannot both satisfy their FK unless the row also
lies about which company it belongs to, and every read in
``app/code_evidence.py`` filters on that column besides.

HONEST LIMITS RECORDED IN THE SCHEMA, NOT GLOSSED OVER
``code_quality_reports.coverage`` is always ``{"available": false, "reason":
...}`` — coverage needs instrumented execution and this analyser never runs
candidate code. ``analyser`` is ``python-ast`` only for Python; every other
language (and any Python source ``ast.parse`` cannot parse) gets
``pygments-tokens``, which the UI must label an approximation.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f4c6e8a0b2d7"
down_revision: str | None = "e3b5d7f9a1c5"
branch_labels: str | None = None
depends_on: str | None = None

CODE_QUALITY_ANALYSERS = ("python-ast", "pygments-tokens")
CODE_QUALITY_STATUSES = ("complete", "unsupported", "failed", "skipped")
CODE_SIMILARITY_REFERENCE_KINDS = ("submission", "reference_solution")
CODE_INTEGRITY_OUTCOMES = ("no_concern", "follow_up", "confirmed")


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.TIMESTAMP(timezone=True), nullable=nullable)


# ---------------------------------------------------------------------------
# A submitted/expired attempt's graded result is frozen (the redaction
# exception is the one retention/erasure use to strip coding source+output).
# ---------------------------------------------------------------------------
EXAM_ATTEMPTS_SUBMISSION_FROZEN = """
CREATE OR REPLACE FUNCTION exam_attempts_submission_frozen() RETURNS trigger AS $$
BEGIN
    IF OLD.status NOT IN ('submitted', 'expired') THEN
        RETURN NEW;  -- still in_progress -- nothing graded to freeze yet
    END IF;

    IF OLD.code_redacted_at IS NULL AND NEW.code_redacted_at IS NOT NULL THEN
        -- The one exception: retention (app/code_evidence.py::purge) or DPDP
        -- erasure (admin_ops step 5h) stripping candidate source and program
        -- stdout/stderr. Only answers/graded_snapshot may move with it -- the
        -- score, status and timestamps this attempt was graded with do not.
        IF NEW.status IS DISTINCT FROM OLD.status
           OR NEW.score_raw IS DISTINCT FROM OLD.score_raw
           OR NEW.score_max IS DISTINCT FROM OLD.score_max
           OR NEW.score_percent IS DISTINCT FROM OLD.score_percent
           OR NEW.passed IS DISTINCT FROM OLD.passed
           OR NEW.started_at IS DISTINCT FROM OLD.started_at
           OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at THEN
            RAISE EXCEPTION
                'redacting exam attempt % changes only answers, graded_snapshot and code_redacted_at',
                OLD.id;
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.answers IS DISTINCT FROM OLD.answers
       OR NEW.graded_snapshot IS DISTINCT FROM OLD.graded_snapshot
       OR NEW.score_raw IS DISTINCT FROM OLD.score_raw
       OR NEW.score_max IS DISTINCT FROM OLD.score_max
       OR NEW.score_percent IS DISTINCT FROM OLD.score_percent
       OR NEW.passed IS DISTINCT FROM OLD.passed
       OR NEW.status IS DISTINCT FROM OLD.status
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at THEN
        RAISE EXCEPTION 'exam attempt % is % and its graded result is fixed', OLD.id, OLD.status;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# ---------------------------------------------------------------------------
# Reports / fingerprints / signals: system evidence, immutable once written.
# UPDATE is always refused; DELETE stays legal (retention, erasure).
# ---------------------------------------------------------------------------
CODE_EVIDENCE_IMMUTABLE = """
CREATE OR REPLACE FUNCTION code_evidence_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is immutable once written (% refused)', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;
"""

# ---------------------------------------------------------------------------
# Findings: HUMAN evidence. No UPDATE except a controlled redaction, clearing
# a followed-up signal_id, or a one-time supersede. DELETE only as a cascade
# (the applicant/company itself going) -- there is no direct DELETE route.
# ---------------------------------------------------------------------------
CODE_INTEGRITY_FINDINGS_GUARD = """
CREATE OR REPLACE FUNCTION code_integrity_findings_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        -- Cascade only: the attempt (or its company) is going. There is no
        -- direct DELETE route in the app -- retention and erasure both
        -- redact the rationale, never remove the row.
        IF EXISTS (SELECT 1 FROM exam_attempts a WHERE a.id = OLD.attempt_id) THEN
            RAISE EXCEPTION 'code integrity finding % is kept, not deleted', OLD.id;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.redacted_at IS NOT NULL OR NEW.superseded_at IS NOT NULL THEN
            RAISE EXCEPTION 'a code integrity finding arrives neither redacted nor superseded';
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE
    IF OLD.redacted_at IS NOT NULL THEN
        RAISE EXCEPTION 'code integrity finding % is redacted and is fixed', OLD.id;
    END IF;

    IF NEW.redacted_at IS NOT NULL THEN
        -- A redaction: rationale -> the fixed marker, together with
        -- redacted_at, and nothing else in this same statement.
        IF NEW.rationale <> '[redacted]' THEN
            RAISE EXCEPTION 'a redacted rationale becomes the fixed marker [redacted]';
        END IF;
        IF NEW.outcome IS DISTINCT FROM OLD.outcome
           OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
           OR NEW.coding_question_id IS DISTINCT FROM OLD.coding_question_id
           OR NEW.enrolment_id IS DISTINCT FROM OLD.enrolment_id
           OR NEW.recorded_by_user_id IS DISTINCT FROM OLD.recorded_by_user_id
           OR NEW.signal_id IS DISTINCT FROM OLD.signal_id
           OR NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id
           OR NEW.superseded_at IS DISTINCT FROM OLD.superseded_at THEN
            RAISE EXCEPTION 'a redaction changes only the rationale and redacted_at';
        END IF;
        RETURN NEW;
    END IF;

    -- Not a redaction: signal_id may only ever become NULL (a followed-up
    -- signal being cleared once acted on), and nothing else may move with it.
    IF NEW.signal_id IS DISTINCT FROM OLD.signal_id THEN
        IF NEW.signal_id IS NOT NULL THEN
            RAISE EXCEPTION
                'code integrity finding % keeps which signal it followed up, or clears it', OLD.id;
        END IF;
        IF NEW.rationale IS DISTINCT FROM OLD.rationale OR NEW.outcome IS DISTINCT FROM OLD.outcome
           OR NEW.superseded_at IS DISTINCT FROM OLD.superseded_at THEN
            RAISE EXCEPTION 'clearing signal_id changes nothing else';
        END IF;
        RETURN NEW;
    END IF;

    -- Not a redaction, not a signal clear: an already-superseded finding
    -- accepts no further UPDATE at all. Checked on OLD alone, deliberately
    -- BEFORE comparing to NEW -- `now()` is fixed for the whole transaction
    -- in Postgres, so a second supersede attempt sharing a transaction with
    -- the first (as this migration's own guarantee tests do) would write
    -- back the SAME timestamp, and a NEW-vs-OLD DISTINCT check would then
    -- see no difference at all and fall through to the generic error below
    -- instead of naming what actually happened.
    IF OLD.superseded_at IS NOT NULL THEN
        RAISE EXCEPTION 'code integrity finding % is already superseded', OLD.id;
    END IF;

    -- Superseding for the first time: only superseded_at may move, NULL to a
    -- value (a NEW finding row, inserted in the same transaction, names this
    -- one via supersedes_id -- app/code_evidence.py::record_finding does both).
    IF NEW.superseded_at IS DISTINCT FROM OLD.superseded_at THEN
        IF NEW.rationale IS DISTINCT FROM OLD.rationale OR NEW.outcome IS DISTINCT FROM OLD.outcome THEN
            RAISE EXCEPTION 'superseding changes only superseded_at';
        END IF;
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'code integrity finding % keeps its scope and content outside a redaction', OLD.id;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # -----------------------------------------------------------------
    # exam_attempts / coding_questions: the composite unique keys every new
    # table's composite FKs need, plus the one new column and the lookback
    # index the sweep's "pending" query uses.
    # -----------------------------------------------------------------
    op.create_unique_constraint(
        "uq_exam_attempts_id_company", "exam_attempts", ["id", "company_id"]
    )
    op.create_unique_constraint(
        "uq_coding_questions_id_company", "coding_questions", ["id", "company_id"]
    )
    op.add_column("exam_attempts", sa.Column("code_redacted_at", sa.TIMESTAMP(timezone=True),
                                              nullable=True))
    op.create_index(
        "ix_exam_attempts_pending_code_analysis", "exam_attempts", ["submitted_at"],
        postgresql_where=sa.text("status IN ('submitted','expired') AND deleted_at IS NULL"),
    )

    # -----------------------------------------------------------------
    # code_quality_reports
    # -----------------------------------------------------------------
    op.create_table(
        "code_quality_reports",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("coding_question_id", sa.Uuid(), nullable=False),
        sa.Column("exam_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("analyser", sa.Text(), nullable=False),
        sa.Column("analyser_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("findings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("coverage", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        # Never the source -- see app/code_evidence.py's analyse_pending: on
        # failure only the exception's TYPE NAME is stored.
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_code_quality_reports"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_code_quality_reports_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["attempt_id", "company_id"], ["exam_attempts.id", "exam_attempts.company_id"],
            name="fk_code_quality_reports_attempt", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["coding_question_id", "company_id"],
            ["coding_questions.id", "coding_questions.company_id"],
            name="fk_code_quality_reports_question", ondelete="CASCADE",
        ),
        sa.CheckConstraint(f"analyser IN {CODE_QUALITY_ANALYSERS}",
                           name="ck_code_quality_reports_analyser"),
        sa.CheckConstraint(f"status IN {CODE_QUALITY_STATUSES}",
                           name="ck_code_quality_reports_status"),
        sa.UniqueConstraint("attempt_id", "coding_question_id", "analyser_version",
                            name="uq_code_quality_reports_attempt_question_version"),
    )
    op.create_index("ix_code_quality_reports_company_attempt", "code_quality_reports",
                    ["company_id", "attempt_id"])

    # -----------------------------------------------------------------
    # code_fingerprints
    # -----------------------------------------------------------------
    op.create_table(
        "code_fingerprints",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("coding_question_id", sa.Uuid(), nullable=False),
        sa.Column("hashes", postgresql.ARRAY(sa.BigInteger()), nullable=False),
        sa.Column("lines", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_code_fingerprints"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_code_fingerprints_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["attempt_id", "company_id"], ["exam_attempts.id", "exam_attempts.company_id"],
            name="fk_code_fingerprints_attempt", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["coding_question_id", "company_id"],
            ["coding_questions.id", "coding_questions.company_id"],
            name="fk_code_fingerprints_question", ondelete="CASCADE",
        ),
        sa.CheckConstraint("token_count >= 0", name="ck_code_fingerprints_token_count"),
        sa.UniqueConstraint("attempt_id", "coding_question_id", "algorithm_version",
                            name="uq_code_fingerprints_attempt_question_version"),
    )
    op.create_index("ix_code_fingerprints_hashes", "code_fingerprints", ["hashes"],
                    postgresql_using="gin")
    op.create_index("ix_code_fingerprints_question", "code_fingerprints",
                    ["coding_question_id", "algorithm_version"])

    # -----------------------------------------------------------------
    # code_similarity_signals — SYSTEM evidence
    # -----------------------------------------------------------------
    op.create_table(
        "code_similarity_signals",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("coding_question_id", sa.Uuid(), nullable=False),
        sa.Column("exam_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_low_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_high_id", sa.Uuid(), nullable=True),
        sa.Column("reference_kind", sa.Text(), nullable=False, server_default="submission"),
        sa.Column("containment_low", sa.Numeric(5, 4), nullable=False),
        sa.Column("containment_high", sa.Numeric(5, 4), nullable=True),
        sa.Column("jaccard", sa.Numeric(5, 4), nullable=False),
        sa.Column("shared_fingerprints", sa.Integer(), nullable=False),
        sa.Column("tokens_low", sa.Integer(), nullable=False),
        sa.Column("tokens_high", sa.Integer(), nullable=True),
        sa.Column("matched_regions", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("thresholds", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_code_similarity_signals"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_code_similarity_signals_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["coding_question_id", "company_id"],
            ["coding_questions.id", "coding_questions.company_id"],
            name="fk_code_similarity_signals_question", ondelete="CASCADE",
        ),
        # BOTH sides of the pair resolve through the SAME company_id column on
        # THIS row -- a cross-tenant pair is unrepresentable, not merely
        # filtered: the row would have to lie about its own tenant to satisfy
        # both FKs at once.
        sa.ForeignKeyConstraint(
            ["attempt_low_id", "company_id"], ["exam_attempts.id", "exam_attempts.company_id"],
            name="fk_code_similarity_signals_attempt_low", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_high_id", "company_id"], ["exam_attempts.id", "exam_attempts.company_id"],
            name="fk_code_similarity_signals_attempt_high", ondelete="CASCADE",
        ),
        sa.CheckConstraint(f"reference_kind IN {CODE_SIMILARITY_REFERENCE_KINDS}",
                           name="ck_code_similarity_signals_reference_kind"),
        sa.CheckConstraint(
            "(attempt_high_id IS NOT NULL AND attempt_low_id < attempt_high_id)"
            " OR (reference_kind = 'reference_solution' AND attempt_high_id IS NULL)",
            name="ck_code_similarity_signals_pair_order",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(matched_regions) = 'array' AND jsonb_array_length(matched_regions) <= 20",
            name="ck_code_similarity_signals_regions_cap",
        ),
        # composite FK target for code_integrity_findings.signal_id.
        sa.UniqueConstraint("id", "company_id", name="uq_code_similarity_signals_id_company"),
    )
    # Two partial unique indexes rather than one constraint: a plain UNIQUE
    # treats every NULL attempt_high_id as distinct, which would let the same
    # (attempt, question, version) reference-solution comparison be inserted
    # any number of times.
    op.create_index(
        "uq_code_similarity_signals_pair", "code_similarity_signals",
        ["attempt_low_id", "attempt_high_id", "coding_question_id", "algorithm_version"],
        unique=True, postgresql_where=sa.text("reference_kind = 'submission'"),
    )
    op.create_index(
        "uq_code_similarity_signals_reference", "code_similarity_signals",
        ["attempt_low_id", "coding_question_id", "algorithm_version"],
        unique=True, postgresql_where=sa.text("reference_kind = 'reference_solution'"),
    )
    op.create_index("ix_code_similarity_signals_exam", "code_similarity_signals",
                    ["company_id", "exam_id"])

    # -----------------------------------------------------------------
    # code_integrity_findings — HUMAN evidence
    # -----------------------------------------------------------------
    op.create_table(
        "code_integrity_findings",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("coding_question_id", sa.Uuid(), nullable=False),
        sa.Column("signal_id", sa.Uuid(), nullable=True),
        sa.Column("enrolment_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("recorded_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        _ts("superseded_at"),
        _ts("redacted_at"),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_code_integrity_findings"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_code_integrity_findings_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["attempt_id", "company_id"], ["exam_attempts.id", "exam_attempts.company_id"],
            name="fk_code_integrity_findings_attempt", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["coding_question_id", "company_id"],
            ["coding_questions.id", "coding_questions.company_id"],
            name="fk_code_integrity_findings_question", ondelete="CASCADE",
        ),
        # RESTRICT, not SET NULL: this is a COMPOSITE FK, and Postgres's
        # ON DELETE SET NULL for a composite FK nulls EVERY column the
        # constraint names -- including company_id, which is NOT NULL here.
        # (A real bug this migration shipped with once: deleting a signal a
        # finding referenced raised a NOT NULL violation on
        # code_integrity_findings.company_id instead of clearing signal_id.)
        # app/code_evidence.py::purge and admin_ops's erasure step 5h both
        # NULL signal_id themselves, on every finding that names a signal
        # about to be deleted, BEFORE deleting it — so RESTRICT never
        # actually fires along that path; it exists to fail loudly if some
        # future caller deletes a signal without doing that.
        sa.ForeignKeyConstraint(
            ["signal_id", "company_id"],
            ["code_similarity_signals.id", "code_similarity_signals.company_id"],
            name="fk_code_integrity_findings_signal", ondelete="RESTRICT",
        ),
        # Same reasoning: enrolments are never hard-deleted (DPDP-7 keeps
        # them), so this never fires today -- RESTRICT over SET NULL is still
        # the right default for a composite FK onto a NOT NULL column.
        sa.ForeignKeyConstraint(
            ["enrolment_id", "company_id"], ["enrolments.id", "enrolments.company_id"],
            name="fk_code_integrity_findings_enrolment", ondelete="RESTRICT",
        ),
        # NOT NULL -- a person always recorded this, so RESTRICT rather than
        # SET NULL (which SET NULL cannot honour on a NOT NULL column anyway).
        # Users are anonymised by DPDP erasure, never hard-deleted, so this
        # never actually fires in practice -- the accommodation_events
        # precedent is SET NULL only because THAT column is nullable.
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"],
                                name="fk_code_integrity_findings_recorder", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["supersedes_id", "company_id"],
            ["code_integrity_findings.id", "code_integrity_findings.company_id"],
            name="fk_code_integrity_findings_supersedes", ondelete="SET NULL",
            deferrable=True, initially="DEFERRED",
        ),
        sa.CheckConstraint(f"outcome IN {CODE_INTEGRITY_OUTCOMES}",
                           name="ck_code_integrity_findings_outcome"),
        # The redaction marker itself ('[redacted]', 11 chars) is shorter
        # than the 20-char floor a human-authored rationale must clear, so it
        # is named explicitly rather than making every redacted row a CHECK
        # violation.
        sa.CheckConstraint(
            "rationale = '[redacted]' OR char_length(rationale) BETWEEN 20 AND 2000",
            name="ck_code_integrity_findings_rationale_len",
        ),
        # composite FK target for its own self-referential supersedes_id.
        sa.UniqueConstraint("id", "company_id", name="uq_code_integrity_findings_id_company"),
    )
    op.create_index("ix_code_integrity_findings_attempt", "code_integrity_findings",
                    ["company_id", "attempt_id"])

    for sql in (
        EXAM_ATTEMPTS_SUBMISSION_FROZEN, CODE_EVIDENCE_IMMUTABLE, CODE_INTEGRITY_FINDINGS_GUARD,
    ):
        op.execute(sql)
    op.execute(
        "CREATE TRIGGER exam_attempts_submission_frozen"
        " BEFORE UPDATE ON exam_attempts"
        " FOR EACH ROW EXECUTE FUNCTION exam_attempts_submission_frozen()"
    )
    for table in ("code_quality_reports", "code_fingerprints", "code_similarity_signals"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable"
            f" BEFORE UPDATE ON {table}"
            f" FOR EACH ROW EXECUTE FUNCTION code_evidence_immutable()"
        )
    op.execute(
        "CREATE TRIGGER code_integrity_findings_guard"
        " BEFORE INSERT OR UPDATE OR DELETE ON code_integrity_findings"
        " FOR EACH ROW EXECUTE FUNCTION code_integrity_findings_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS code_integrity_findings_guard ON code_integrity_findings")
    op.execute("DROP FUNCTION IF EXISTS code_integrity_findings_guard()")
    for table in ("code_quality_reports", "code_fingerprints", "code_similarity_signals"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS code_evidence_immutable()")
    op.execute("DROP TRIGGER IF EXISTS exam_attempts_submission_frozen ON exam_attempts")
    op.execute("DROP FUNCTION IF EXISTS exam_attempts_submission_frozen()")

    op.drop_index("ix_code_integrity_findings_attempt", table_name="code_integrity_findings")
    op.drop_table("code_integrity_findings")

    op.drop_index("ix_code_similarity_signals_exam", table_name="code_similarity_signals")
    op.drop_index("uq_code_similarity_signals_reference", table_name="code_similarity_signals")
    op.drop_index("uq_code_similarity_signals_pair", table_name="code_similarity_signals")
    op.drop_table("code_similarity_signals")

    op.drop_index("ix_code_fingerprints_question", table_name="code_fingerprints")
    op.drop_index("ix_code_fingerprints_hashes", table_name="code_fingerprints")
    op.drop_table("code_fingerprints")

    op.drop_index("ix_code_quality_reports_company_attempt", table_name="code_quality_reports")
    op.drop_table("code_quality_reports")

    op.drop_index("ix_exam_attempts_pending_code_analysis", table_name="exam_attempts")
    op.drop_column("exam_attempts", "code_redacted_at")
    op.drop_constraint("uq_coding_questions_id_company", "coding_questions", type_="unique")
    op.drop_constraint("uq_exam_attempts_id_company", "exam_attempts", type_="unique")
