"""Talent pools, rediscovery consent and the pool trail — PH5-E3.

Revision ID: e4a6c8b0d2f7
Revises: d3f5b7a9c1e6
Create Date: 2026-09-24

WHAT THIS ADDS
Three tables for a company's own talent pools, plus TWO INDEX CHANGES on
``dpdp_consent_ledger`` that let the existing ledger carry a new kind of
consent — a per-company, expiring, withdrawable opt-in to be considered for
FUTURE openings (``consent_type = 'talent_pool_rediscovery'``, ``purpose =
'rediscovery'``).

  * ``talent_pools``        — a company's named list (name, description, who
                             made it). Company-authored configuration; no
                             candidate column.
  * ``talent_pool_members`` — one candidate's membership of one pool, with
                             HR's note, the frozen "why this matched"
                             snapshot as at add time, and the
                             evidence-freshness band as at add time.
  * ``talent_pool_events``  — append-only history of a pool and its
                             membership, on the ``document_events`` /
                             ``task_events`` precedent.

Composite ``(id, company_id)`` foreign keys throughout, on the
``candidate_documents`` (``c1e3a5b7d9f4``) and ``corpus_*``
(``d3f5b7a9c1e6``) precedent, so a child row cannot point at another tenant's
parent even if a caller ever passed the wrong ids.
``uq_applicants_id_company`` already exists (``a7b8c9d0e1f2``), so
``applicants`` can be a composite-FK parent here.

WHY THE CONSENT IS NOT A NEW TABLE
A derived ``rediscovery_consents`` mirror is the tempting design, and it is
the one PH4-D4's security re-review caught disagreeing with the ledger
(``c8e0a2b4d6f8``, NEW-7). ``dpdp_consent_ledger`` can carry this consent
directly through the exact route PH4-D4 cut for per-submission task consent:
narrow ``ix_dpdp_consent_active_unique`` to exclude the new type
(``a5d7f9b1c3e8:86-90`` did this for ``assessment_submission``), and add a
second partial unique index over an ``evidence`` JSONB expression. So the
LEDGER IS THE RECORD: withdrawal, erasure-time revocation
(``admin_ops/app/routers/erasure.py`` revokes every row for a user at REQUEST
time) and the 12-month expiry need no synchronisation, because there is
nothing to synchronise.

``ix_dpdp_consent_rediscovery_unique`` is keyed on
``(user_id, (evidence ->> 'company_id'))``: this consent is unique per
(person, COMPANY), not per (person, type, purpose) — each company's opt-in is
its own decision, so one person may hold a separate active row per company
they have dealt with. (``uq_applicants_user_id``, migration ``a7b8c9d0e1f2``,
is a PLATFORM-WIDE unique index on ``applicants.user_id``, so today one
account holds at most one applicant row anywhere — the per-company shape here
is about the CONSENT, which is deliberately not limited to one company the
way an applicant row currently is, and is the reason ``app/rediscovery.py``'s
eligibility join checks ``evidence ->> 'company_id'`` rather than trusting
``user_id`` alone. See that module's docstring and ``docs/DATA-FLOW.md``'s
rediscovery-consent row for what changes if that index is ever relaxed.) That
index is also the one the eligibility query in ``app/rediscovery.py`` reads
through.

The ``evidence ->> 'company_id'`` string comparison has no schema-level
guarantee that the text is a well-formed, lowercase-hyphenated UUID; the
controls are that ``app/rediscovery.py::record_opt_in`` is its ONLY writer
(the ``public_apply.py::_record_apply_consent`` precedent), a DB round-trip
test that a row that function writes satisfies the eligibility predicate, and
this index — which at least makes a duplicate loud. A generated column, or a
real ``company_id`` column on the ledger, would be stronger but changes a
table every service reads for the sake of one consent type.

A NOTE FOR ANYONE WRITING SQL AGAINST ``evidence``: the column is ``json``,
not ``jsonb``, on the database — ``app/models.py::DpdpConsent`` declares
``JSONB``, but the column was created as ``json`` by the original migration
and nothing has migrated it. Reads (``evidence ->> 'key'``) are identical, so
the index above and the eligibility join are unaffected; anything that
CONCATENATES (``||``) has to cast both ways (``evidence::jsonb || … ::jsonb``
then ``::json``), because there is no implicit json/jsonb coercion. Not
changed here: rewriting a column every service reads to fix a type mismatch
nothing currently depends on is a separate, reviewable change.

``expires_at_iso`` in ``evidence`` is DERIVED AND STORED FOR DISPLAY ONLY.
Eligibility computes expiry from ``granted_at`` (see
``app/rediscovery.py::ELIGIBLE_CTE``), so a wrong or stale ``expires_at_iso``
can never widen who is findable.

ALSO HERE (code review FIX 2): ``application_drafts.rediscovery_opt_in``
------------------------------------------------------------------------
``boolean NOT NULL DEFAULT false``. The rediscovery checkbox was wired only to
the single-shot ``POST /apply/{id}``; the draft path had nowhere to keep a
tick and ``submit_draft`` never looked for one, so a candidate who ticked it
and clicked "Save for later" had their consent silently dropped — worse than
never offering the checkbox at all. This migration is unmerged and has never
been deployed, so the column joins it here rather than opening a second
migration for one boolean. ``POST /apply/{id}/draft`` stores it at the first
save; ``submit_draft`` reads it back and calls ``app/rediscovery.py
::record_opt_in`` on exactly the terms the single-shot path already uses.

WHAT DOES NOT LIVE HERE
  * The erasure-inventory declarations for these three tables
    (``services/admin_ops/app/erasure_executor.py``'s ``ERASED_TABLES`` /
    ``EXCLUDED_TABLES``). ``services/admin_ops/tests/test_erasure_inventory.py``
    is RED from the moment this migration lands until they are written — that
    is the mechanism working, not a defect in this file, and this migration
    deliberately does not pre-empt the erasure owner's reasons.
  * Any ORM model. Like ``corpus_*``, these tables are read and written by raw
    parameterised SQL in the service layer; nothing here is mapped in
    ``app/models.py``.
  * ``record_opt_in``'s own writes, the eligibility CTE and the rediscovery
    search — ``app/rediscovery.py``.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e4a6c8b0d2f7"
down_revision: str | None = "d3f5b7a9c1e6"
branch_labels: str | None = None
depends_on: str | None = None

# The new consent pair. Duplicated as literals in app/rediscovery.py
# (REDISCOVERY_CONSENT_TYPE / REDISCOVERY_PURPOSE) and pinned equal by
# tests/unit/test_ph5_e3_rediscovery_rules.py — a migration cannot import
# application code, so the test is what keeps the two in step.
REDISCOVERY_CONSENT_TYPE = "talent_pool_rediscovery"
TASK_CONSENT_TYPE = "assessment_submission"

MEMBER_SOURCES = ("manual", "rediscovery")
FRESHNESS_BANDS = ("fresh", "ageing", "stale", "unverifiable", "none")
POOL_EVENTS = (
    "created", "renamed", "described", "archived", "restored", "deleted",
    "member_added", "member_removed", "member_evidence_reviewed", "member_invited",
)

# ``a5d7f9b1c3e8``'s shape, which is what is on the database now, and the
# narrowed shape this migration installs. Written out rather than imported so
# that a reader of THIS file can see both, and so a downgrade does not depend
# on another migration module's private names.
_CONSENT_ACTIVE_UNIQUE_BEFORE = (
    "CREATE UNIQUE INDEX ix_dpdp_consent_active_unique"
    " ON dpdp_consent_ledger (user_id, consent_type, purpose)"
    " WHERE granted = TRUE AND revoked_at IS NULL"
    f" AND consent_type <> '{TASK_CONSENT_TYPE}'"
)
_CONSENT_ACTIVE_UNIQUE_AFTER = (
    "CREATE UNIQUE INDEX ix_dpdp_consent_active_unique"
    " ON dpdp_consent_ledger (user_id, consent_type, purpose)"
    " WHERE granted = TRUE AND revoked_at IS NULL"
    f" AND consent_type NOT IN ('{TASK_CONSENT_TYPE}', '{REDISCOVERY_CONSENT_TYPE}')"
)
_REDISCOVERY_UNIQUE = (
    "CREATE UNIQUE INDEX ix_dpdp_consent_rediscovery_unique"
    " ON dpdp_consent_ledger (user_id, (evidence ->> 'company_id'))"
    f" WHERE consent_type = '{REDISCOVERY_CONSENT_TYPE}'"
    " AND granted = TRUE AND revoked_at IS NULL"
)

APPEND_ONLY_FN = """
CREATE OR REPLACE FUNCTION talent_pool_events_append_only() RETURNS trigger AS $$
BEGIN
    -- The ONE licensed UPDATE: the FK's ON DELETE SET NULL nulling
    -- actor_user_id when a staff account is deleted. Whitelisted exactly as
    -- preboarding_append_only() does (c1e3a5b7d9f4), by proving that nothing
    -- else on the row changed.
    IF TG_OP = 'UPDATE' AND NEW.actor_user_id IS NULL AND OLD.actor_user_id IS NOT NULL
       AND (to_jsonb(NEW) - 'actor_user_id') = (to_jsonb(OLD) - 'actor_user_id') THEN
        RETURN NEW;
    END IF;
    -- Deleted only once the pool it names is already gone (companies are soft
    -- deleted on this platform, so this branch is defence in depth rather than
    -- an expected path). NOT deleted by DPDP erasure: an event carries facts
    -- only -- action, actor, ids, a freshness band, a note LENGTH -- and the
    -- applicant it names is anonymised by erasure step 6.
    IF TG_OP = 'DELETE' AND NOT EXISTS (
        SELECT 1 FROM talent_pools p WHERE p.id = OLD.pool_id
    ) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'talent_pool_events is append-only';
END;
$$ LANGUAGE plpgsql;
"""
APPEND_ONLY_HOOK = """
CREATE TRIGGER talent_pool_events_append_only
    BEFORE UPDATE OR DELETE ON talent_pool_events
    FOR EACH ROW EXECUTE FUNCTION talent_pool_events_append_only()
"""


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.TIMESTAMP(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "talent_pools",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        # HR's own words about the POOL ("SDET shortlist, Q3 backfill"), never
        # about a person. A sentence about a candidate belongs on the member
        # row's `note`, which erasure deletes.
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
        # Archived = HR stops using a pool without losing it. Deleted = soft
        # delete, so the events that name it still resolve.
        _ts("archived_at"),
        _ts("deleted_at"),
        sa.PrimaryKeyConstraint("id", name="pk_talent_pools"),
        sa.UniqueConstraint("id", "company_id", name="uq_talent_pools_id_company"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_talent_pools_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                name="fk_talent_pools_created_by", ondelete="SET NULL"),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="ck_talent_pools_name"),
        sa.CheckConstraint("description IS NULL OR char_length(description) <= 1000",
                           name="ck_talent_pools_description"),
    )
    op.alter_column("talent_pools", "created_at", server_default=sa.text("now()"))
    op.alter_column("talent_pools", "updated_at", server_default=sa.text("now()"))
    op.create_index("ix_talent_pools_company", "talent_pools", ["company_id"],
                    postgresql_where=sa.text("deleted_at IS NULL"))
    op.execute(
        "CREATE UNIQUE INDEX uq_talent_pools_company_name"
        " ON talent_pools (company_id, lower(name)) WHERE deleted_at IS NULL"
    )

    op.create_table(
        "talent_pool_members",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("pool_id", sa.Uuid(), nullable=False),
        sa.Column("applicant_id", sa.Uuid(), nullable=False),
        # 'manual' is a name HR typed into the pool; 'rediscovery' came off a
        # rediscovery search result, which is why match_reason exists.
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("added_by_user_id", sa.Uuid(), nullable=True),
        _ts("added_at", nullable=False),
        # HR's reason, about a person. Deleted with the row on erasure, and
        # NEVER copied into an event or an audit row (only its length is).
        sa.Column("note", sa.Text(), nullable=True),
        # The frozen "why this matched" snapshot as at add time: signal names,
        # contributions, competency ids, scores, timestamps, freshness bands,
        # citation (kind, id, locator). NO CV prose, no scorecard evidence
        # text, no lexical snippet -- app/rediscovery.py::freeze_match_reason
        # is what strips them, and a unit test pins that it does.
        sa.Column("match_reason", postgresql.JSONB(), nullable=True),
        sa.Column("evidence_freshness", sa.Text(), nullable=True),
        # The criterion-14 control: a named person recorded that they looked at
        # evidence this old. Reviewing does NOT change the band -- the evidence
        # is still two years old -- it records that somebody accepted it.
        _ts("evidence_reviewed_at"),
        sa.Column("evidence_reviewed_by_user_id", sa.Uuid(), nullable=True),
        # Soft removal, so re-adding is a new row and the history survives.
        _ts("removed_at"),
        sa.Column("removed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("removed_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_talent_pool_members"),
        sa.UniqueConstraint("id", "company_id", name="uq_talent_pool_members_id_company"),
        sa.ForeignKeyConstraint(
            ["pool_id", "company_id"], ["talent_pools.id", "talent_pools.company_id"],
            name="fk_talent_pool_members_pool", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["applicant_id", "company_id"], ["applicants.id", "applicants.company_id"],
            name="fk_talent_pool_members_applicant", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["added_by_user_id"], ["users.id"],
                                name="fk_talent_pool_members_added_by", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["evidence_reviewed_by_user_id"], ["users.id"],
                                name="fk_talent_pool_members_reviewed_by", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["removed_by_user_id"], ["users.id"],
                                name="fk_talent_pool_members_removed_by", ondelete="SET NULL"),
        sa.CheckConstraint(f"source IN {MEMBER_SOURCES}", name="ck_talent_pool_members_source"),
        sa.CheckConstraint("note IS NULL OR char_length(note) <= 500",
                           name="ck_talent_pool_members_note"),
        sa.CheckConstraint("removed_reason IS NULL OR char_length(removed_reason) <= 200",
                           name="ck_talent_pool_members_removed_reason_len"),
        # A reason for a removal that did not happen is a contradiction.
        sa.CheckConstraint("removed_reason IS NULL OR removed_at IS NOT NULL",
                           name="ck_talent_pool_members_removed_reason"),
        sa.CheckConstraint(
            f"evidence_freshness IS NULL OR evidence_freshness IN {FRESHNESS_BANDS}",
            name="ck_talent_pool_members_freshness",
        ),
        # A named reviewer with no timestamp is a contradiction. The reverse is
        # NOT: the reviewer FK is ON DELETE SET NULL, so a review whose author's
        # staff account is later deleted keeps its date and loses its name.
        sa.CheckConstraint(
            "evidence_reviewed_by_user_id IS NULL OR evidence_reviewed_at IS NOT NULL",
            name="ck_talent_pool_members_reviewed_pair",
        ),
    )
    op.alter_column("talent_pool_members", "added_at", server_default=sa.text("now()"))
    op.execute(
        "CREATE UNIQUE INDEX uq_talent_pool_members_live"
        " ON talent_pool_members (pool_id, applicant_id) WHERE removed_at IS NULL"
    )
    op.create_index("ix_talent_pool_members_company_pool", "talent_pool_members",
                    ["company_id", "pool_id"], postgresql_where=sa.text("removed_at IS NULL"))
    # For erasure step 5k (delete every membership of one candidate) and for
    # "which pools is this person in?" on the applicant page.
    op.create_index("ix_talent_pool_members_applicant", "talent_pool_members", ["applicant_id"])

    op.create_table(
        "talent_pool_events",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("pool_id", sa.Uuid(), nullable=False),
        # NULL for a pool-level event (created, renamed, archived). Applicants
        # are anonymised, never deleted, so this never dangles.
        sa.Column("applicant_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        # FACTS ONLY -- the app/corpus.py::_audit rule and the task_events
        # exclusion reason: action, ids, counts, a freshness band, a note
        # LENGTH. Never the note, never the removal reason text, never a name,
        # never CV or scorecard prose.
        sa.Column("details", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_talent_pool_events"),
        sa.ForeignKeyConstraint(
            ["pool_id", "company_id"], ["talent_pools.id", "talent_pools.company_id"],
            name="fk_talent_pool_events_pool", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["applicant_id", "company_id"], ["applicants.id", "applicants.company_id"],
            name="fk_talent_pool_events_applicant", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"],
                                name="fk_talent_pool_events_actor", ondelete="SET NULL"),
        sa.CheckConstraint(f"action IN {POOL_EVENTS}", name="ck_talent_pool_events_action"),
        sa.CheckConstraint("actor_type IN ('user', 'system')",
                           name="ck_talent_pool_events_actor_type"),
    )
    op.alter_column("talent_pool_events", "created_at", server_default=sa.text("now()"))
    op.create_index("ix_talent_pool_events_pool", "talent_pool_events",
                    ["pool_id", "created_at"])

    for sql in (APPEND_ONLY_FN, APPEND_ONLY_HOOK):
        op.execute(sql)

    # ------------------------------------------------------------------
    # dpdp_consent_ledger: two index changes, no new column.
    # ------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS ix_dpdp_consent_active_unique")
    op.execute(_CONSENT_ACTIVE_UNIQUE_AFTER)
    op.execute("DROP INDEX IF EXISTS ix_dpdp_consent_rediscovery_unique")
    op.execute(_REDISCOVERY_UNIQUE)

    # ------------------------------------------------------------------
    # application_drafts: one new column (code review FIX 2). See the
    # module docstring's "ALSO HERE" section — this migration is unmerged,
    # so the column joins it here rather than opening a second migration.
    # ------------------------------------------------------------------
    op.add_column(
        "application_drafts",
        sa.Column(
            "rediscovery_opt_in", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("application_drafts", "rediscovery_opt_in")

    op.execute("DROP INDEX IF EXISTS ix_dpdp_consent_rediscovery_unique")
    op.execute("DROP INDEX IF EXISTS ix_dpdp_consent_active_unique")
    # Back to a5d7f9b1c3e8's shape. A rediscovery consent row that still reads
    # granted would now be subject to the one-active-row-per-(user, type,
    # purpose) rule, so a candidate opted in at two companies would break this
    # index. Revoked rather than deleted: the row is the record of a decision
    # the person made, and a downgrade is not a withdrawal by them -- it is the
    # platform removing the feature, which is what `evidence.expiry` says.
    # evidence is a `json` column (see the docstring), so the concatenation
    # casts both ways rather than relying on a coercion that does not exist.
    # nosec B608: the only interpolated value is this module's own consent-type
    # constant. (CI's bandit scan covers services/*/app and shared, not
    # alembic/versions — this is for whoever runs it by hand.)
    op.execute(
        "UPDATE dpdp_consent_ledger SET revoked_at = now(),"  # nosec B608
        " evidence = (coalesce(evidence::jsonb, '{}'::jsonb)"
        "             || '{\"expiry\": \"feature_removed\"}'::jsonb)::json"
        f" WHERE consent_type = '{REDISCOVERY_CONSENT_TYPE}'"
        "   AND granted = TRUE AND revoked_at IS NULL"
    )
    op.execute(_CONSENT_ACTIVE_UNIQUE_BEFORE)

    op.execute("DROP TRIGGER IF EXISTS talent_pool_events_append_only ON talent_pool_events")
    op.execute("DROP FUNCTION IF EXISTS talent_pool_events_append_only()")
    op.drop_index("ix_talent_pool_events_pool", table_name="talent_pool_events")
    op.drop_table("talent_pool_events")

    op.drop_index("ix_talent_pool_members_applicant", table_name="talent_pool_members")
    op.drop_index("ix_talent_pool_members_company_pool", table_name="talent_pool_members")
    op.execute("DROP INDEX IF EXISTS uq_talent_pool_members_live")
    op.drop_table("talent_pool_members")

    op.execute("DROP INDEX IF EXISTS uq_talent_pools_company_name")
    op.drop_index("ix_talent_pools_company", table_name="talent_pools")
    op.drop_table("talent_pools")
