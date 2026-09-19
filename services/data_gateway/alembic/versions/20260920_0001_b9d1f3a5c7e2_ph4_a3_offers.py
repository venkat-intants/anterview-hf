"""Offers — PH4-A3.

Revision ID: b9d1f3a5c7e2
Revises: a8c0e2f4b6d9
Create Date: 2026-09-20

AFTER THE DECISION
A hire is recorded by a person (final_decision, D-05). An offer is what the
company then puts in writing: compensation and terms, approved by the company
super admin (decision D4-2), sent to the candidate over a secure link, and
accepted or declined by the candidate. The decision record is untouched by any
of it — the offer's outcome lands on ``enrolments.offer_outcome``, a separate
column, never on the ledger or the status.

THE LIFECYCLE, AT THE DATABASE
    draft ──submit──▶ pending_approval ──approve──▶ approved ──send──▶ sent
      ▲  ▲               │      │                     │                │
      │  └───recall──────┘      └──reject──▶ rejected │   accept / decline / expire
      └────────reopen─────────────────────────────────┘                ▼
    (withdraw from any state before a candidate's answer)   accepted | declined | expired

``offers_lifecycle`` refuses everything else, on the row itself:
- a new offer is a draft;
- approval needs a reviewer who is neither the submitter nor the author;
- only an approved offer is sent, and it is sent with a link and an expiry;
- only a sent offer is answered, and ACCEPTANCE IS REFUSED ONCE IT HAS EXPIRED —
  the clock is the database's, not the caller's;
- accepted, declined, expired and withdrawn are final: an accepted offer cannot
  quietly become declined, nor the reverse;
- compensation and terms change only while the offer is a draft or was sent
  back: what the approver approved is what the candidate receives.

``offer_events`` is the append-only history (who, when — facts, never prose);
``offer_codes`` holds the one-time codes a candidate must enter to accept or
decline, so a forwarded link alone cannot answer an offer.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "b9d1f3a5c7e2"
down_revision: str | None = "a8c0e2f4b6d9"
branch_labels: str | None = None
depends_on: str | None = None

STATES = ("draft", "pending_approval", "approved", "rejected", "sent", "accepted",
          "declined", "expired", "withdrawn")
EMPLOYMENT = ("full_time", "part_time", "contract", "internship")
PERIODS = ("annual", "monthly", "hourly")
EVENTS = ("created", "updated", "submitted", "recalled", "approved", "rejected", "reopened",
          "sent", "resent", "viewed", "code_requested", "accepted", "declined", "expired",
          "withdrawn", "preboarding_completed", "exported")
OUTCOMES = ("offer_accepted", "offer_declined", "offer_expired", "offer_withdrawn")

LIFECYCLE = """
CREATE OR REPLACE FUNCTION offers_lifecycle() RETURNS trigger AS $$
DECLARE
    -- What an approver approves. Frozen outside draft / rejected.
    content text[] := ARRAY['template_id', 'job_title', 'employment_type', 'start_date',
                            'location', 'base_salary', 'currency', 'pay_period', 'bonus',
                            'equity', 'benefits', 'terms', 'probation_months',
                            'notice_period_days', 'valid_days'];
    k text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        -- Leaves only with its application (the cascade), or while still a draft.
        IF OLD.status <> 'draft'
           AND EXISTS (SELECT 1 FROM enrolments e WHERE e.id = OLD.enrolment_id) THEN
            RAISE EXCEPTION 'offer % is %; it is kept, not deleted', OLD.id, OLD.status;
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' THEN
            RAISE EXCEPTION 'an offer starts as a draft (got %)', NEW.status;
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status IN ('accepted', 'declined', 'expired', 'withdrawn')
       AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION 'offer % is % and final; it cannot become %', OLD.id, OLD.status, NEW.status;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (
               (OLD.status = 'draft' AND NEW.status IN ('pending_approval', 'withdrawn'))
            OR (OLD.status = 'pending_approval'
                AND NEW.status IN ('approved', 'rejected', 'draft', 'withdrawn'))
            OR (OLD.status = 'rejected' AND NEW.status IN ('pending_approval', 'draft', 'withdrawn'))
            OR (OLD.status = 'approved' AND NEW.status IN ('sent', 'draft', 'withdrawn'))
            OR (OLD.status = 'sent'
                AND NEW.status IN ('accepted', 'declined', 'expired', 'withdrawn'))
        ) THEN
            RAISE EXCEPTION 'offer % cannot go from % to %', OLD.id, OLD.status, NEW.status;
        END IF;
        IF NEW.status = 'pending_approval' AND NEW.submitted_by_user_id IS NULL THEN
            RAISE EXCEPTION 'offer % needs a submitter', OLD.id;
        END IF;
        IF NEW.status = 'approved' AND (
               NEW.decided_by_user_id IS NULL
            OR NEW.decided_by_user_id IS NOT DISTINCT FROM OLD.submitted_by_user_id
            OR NEW.decided_by_user_id IS NOT DISTINCT FROM OLD.created_by_user_id) THEN
            RAISE EXCEPTION
                'offer % must be approved by someone other than its author and submitter', OLD.id;
        END IF;
        IF NEW.status = 'rejected' AND (
               NEW.decided_by_user_id IS NULL
            OR NEW.decided_by_user_id IS NOT DISTINCT FROM OLD.submitted_by_user_id) THEN
            RAISE EXCEPTION 'offer % must be sent back by someone other than its submitter', OLD.id;
        END IF;
        IF NEW.status = 'sent' AND (
               NEW.token_hash IS NULL OR NEW.sent_at IS NULL OR NEW.expires_at IS NULL
            OR NEW.expires_at <= now()) THEN
            RAISE EXCEPTION 'offer % is sent with a link and an expiry in the future', OLD.id;
        END IF;
        IF NEW.status = 'accepted' THEN
            IF OLD.expires_at IS NULL OR OLD.expires_at <= now() THEN
                RAISE EXCEPTION 'offer % has expired and can no longer be accepted', OLD.id;
            END IF;
            IF NEW.responded_at IS NULL OR NEW.accepted_name IS NULL THEN
                RAISE EXCEPTION 'offer % is accepted with a name and a time', OLD.id;
            END IF;
        END IF;
        IF NEW.status = 'declined' AND NEW.responded_at IS NULL THEN
            RAISE EXCEPTION 'offer % is declined with a time', OLD.id;
        END IF;
        IF NEW.status = 'expired' AND (OLD.expires_at IS NULL OR OLD.expires_at > now()) THEN
            RAISE EXCEPTION 'offer % has not reached its expiry', OLD.id;
        END IF;
    END IF;

    -- What the approver approved is what the candidate receives.
    IF OLD.status NOT IN ('draft', 'rejected') THEN
        FOREACH k IN ARRAY content LOOP
            IF (to_jsonb(NEW) -> k) IS DISTINCT FROM (to_jsonb(OLD) -> k) THEN
                RAISE EXCEPTION 'offer % is %; its % cannot change (reopen it first)',
                    OLD.id, OLD.status, k;
            END IF;
        END LOOP;
    END IF;
    -- The deadline the candidate was given is the deadline: once sent, neither
    -- it nor the send time moves (a re-send rotates only the link).
    IF OLD.status NOT IN ('draft', 'pending_approval', 'approved', 'rejected') AND (
           NEW.expires_at IS DISTINCT FROM OLD.expires_at
        OR NEW.sent_at IS DISTINCT FROM OLD.sent_at) THEN
        RAISE EXCEPTION 'offer % was sent; its deadline cannot change', OLD.id;
    END IF;
    -- An answer, once given, stays as given.
    IF OLD.responded_at IS NOT NULL AND (
           NEW.responded_at IS DISTINCT FROM OLD.responded_at
        OR (OLD.redacted_at IS NULL AND NEW.redacted_at IS NULL AND (
               NEW.accepted_name IS DISTINCT FROM OLD.accepted_name
            OR NEW.decline_reason IS DISTINCT FROM OLD.decline_reason))) THEN
        RAISE EXCEPTION 'offer % was answered; the answer cannot be rewritten', OLD.id;
    END IF;
    IF OLD.redacted_at IS NOT NULL AND NEW.redacted_at IS DISTINCT FROM OLD.redacted_at THEN
        RAISE EXCEPTION 'offer % was redacted on erasure and stays redacted', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
LIFECYCLE_HOOK = """
CREATE TRIGGER offers_lifecycle
    BEFORE INSERT OR UPDATE OR DELETE ON offers
    FOR EACH ROW EXECUTE FUNCTION offers_lifecycle()
"""
# An offer rests on a hire. A hire used to be movable back into the pipeline
# with no reason and no decision; now it is undone only by a recorded
# rejection, whatever path the write takes.
HIRE_FINAL = """
CREATE OR REPLACE FUNCTION enrolments_hire_undone_only_by_rejection() RETURNS trigger AS $$
BEGIN
    IF OLD.status = 'hired' AND NEW.status IS DISTINCT FROM 'hired'
       AND NEW.status IS DISTINCT FROM 'rejected' THEN
        RAISE EXCEPTION
            'application % is hired; a hire is undone only by recording a rejection', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
HIRE_FINAL_HOOK = """
CREATE TRIGGER enrolments_hire_undone_only_by_rejection
    BEFORE UPDATE OF status ON enrolments
    FOR EACH ROW EXECUTE FUNCTION enrolments_hire_undone_only_by_rejection()
"""
EVENTS_APPEND_ONLY = """
CREATE OR REPLACE FUNCTION offer_events_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.actor_user_id IS NULL AND OLD.actor_user_id IS NOT NULL
       AND (to_jsonb(NEW) - 'actor_user_id') = (to_jsonb(OLD) - 'actor_user_id') THEN
        RETURN NEW;  -- a deleted user's id is nulled; nothing else may change
    END IF;
    IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM offers o WHERE o.id = OLD.offer_id) THEN
        RETURN OLD;  -- the offer itself is going (its application was deleted)
    END IF;
    RAISE EXCEPTION 'offer_events is append-only';
END;
$$ LANGUAGE plpgsql;
"""
EVENTS_HOOK = """
CREATE TRIGGER offer_events_append_only
    BEFORE UPDATE OR DELETE ON offer_events
    FOR EACH ROW EXECUTE FUNCTION offer_events_append_only()
"""


def _stamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    ]


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.TIMESTAMP(timezone=True), nullable=True)


def _user(name: str, table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([name], ["users.id"], name=f"fk_{table}_{name}",
                                   ondelete="SET NULL")


def upgrade() -> None:
    op.create_table(
        "offer_templates",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("employment_type", sa.Text(), nullable=False, server_default="full_time"),
        sa.Column("currency", sa.Text(), nullable=False, server_default="INR"),
        sa.Column("pay_period", sa.Text(), nullable=False, server_default="annual"),
        sa.Column("probation_months", sa.SmallInteger(), nullable=True),
        sa.Column("notice_period_days", sa.SmallInteger(), nullable=True),
        sa.Column("benefits", sa.Text(), nullable=True),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("valid_days", sa.SmallInteger(), nullable=False, server_default="7"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        *_stamps(),
        _ts("deleted_at"),
        sa.PrimaryKeyConstraint("id", name="pk_offer_templates"),
        sa.UniqueConstraint("id", "company_id", name="uq_offer_templates_id_company"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_offer_templates_company", ondelete="CASCADE"),
        _user("created_by_user_id", "offer_templates"),
        _user("updated_by_user_id", "offer_templates"),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="ck_offer_templates_name"),
        sa.CheckConstraint(f"employment_type IN {EMPLOYMENT}", name="ck_offer_templates_employment"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_offer_templates_currency"),
        sa.CheckConstraint(f"pay_period IN {PERIODS}", name="ck_offer_templates_period"),
        sa.CheckConstraint("probation_months IS NULL OR probation_months BETWEEN 0 AND 24",
                           name="ck_offer_templates_probation"),
        sa.CheckConstraint("notice_period_days IS NULL OR notice_period_days BETWEEN 0 AND 365",
                           name="ck_offer_templates_notice"),
        sa.CheckConstraint("benefits IS NULL OR char_length(benefits) <= 4000",
                           name="ck_offer_templates_benefits"),
        sa.CheckConstraint("terms IS NULL OR char_length(terms) <= 20000",
                           name="ck_offer_templates_terms"),
        sa.CheckConstraint("valid_days BETWEEN 1 AND 60", name="ck_offer_templates_valid_days"),
    )
    op.create_index("uq_offer_templates_name", "offer_templates",
                    ["company_id", sa.text("lower(name)")], unique=True,
                    postgresql_where=sa.text("deleted_at IS NULL"))

    op.create_table(
        "offers",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("enrolment_id", sa.Uuid(), nullable=False),
        sa.Column("applicant_id", sa.Uuid(), nullable=False),
        sa.Column("requisition_id", sa.Uuid(), nullable=True),
        sa.Column("template_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("job_title", sa.Text(), nullable=False),
        sa.Column("employment_type", sa.Text(), nullable=False, server_default="full_time"),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("base_salary", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="INR"),
        sa.Column("pay_period", sa.Text(), nullable=False, server_default="annual"),
        sa.Column("bonus", sa.Text(), nullable=True),
        sa.Column("equity", sa.Text(), nullable=True),
        sa.Column("benefits", sa.Text(), nullable=True),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("probation_months", sa.SmallInteger(), nullable=True),
        sa.Column("notice_period_days", sa.SmallInteger(), nullable=True),
        sa.Column("valid_days", sa.SmallInteger(), nullable=False, server_default="7"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("submitted_by_user_id", sa.Uuid(), nullable=True),
        _ts("submitted_at"),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        _ts("decided_at"),
        sa.Column("approval_note", sa.Text(), nullable=True),
        sa.Column("sent_by_user_id", sa.Uuid(), nullable=True),
        _ts("sent_at"),
        _ts("expires_at"),
        sa.Column("token_hash", sa.Text(), nullable=True),
        _ts("first_viewed_at"),
        _ts("responded_at"),
        sa.Column("accepted_name", sa.Text(), nullable=True),
        sa.Column("decline_reason", sa.Text(), nullable=True),
        sa.Column("withdrawn_by_user_id", sa.Uuid(), nullable=True),
        _ts("withdrawn_at"),
        sa.Column("withdraw_reason", sa.Text(), nullable=True),
        _ts("preboarding_completed_at"),
        sa.Column("preboarding_completed_by", sa.Uuid(), nullable=True),
        # Wrong one-time codes over the offer's whole life (security review M1):
        # at the cap, answering and document access lock until HR re-sends.
        sa.Column("code_failures", sa.SmallInteger(), nullable=False, server_default="0"),
        _ts("redacted_at"),
        *_stamps(),
        sa.PrimaryKeyConstraint("id", name="pk_offers"),
        sa.UniqueConstraint("id", "company_id", name="uq_offers_id_company"),
        sa.UniqueConstraint("token_hash", name="uq_offers_token_hash"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_offers_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enrolment_id", "company_id"],
                                ["enrolments.id", "enrolments.company_id"],
                                name="fk_offers_enrolment", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["applicant_id", "company_id"],
                                ["applicants.id", "applicants.company_id"],
                                name="fk_offers_applicant", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requisition_id"], ["job_requisitions.id"],
                                name="fk_offers_requisition", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["template_id"], ["offer_templates.id"],
                                name="fk_offers_template", ondelete="SET NULL"),
        *(_user(c, "offers") for c in ("created_by_user_id", "submitted_by_user_id",
                                       "decided_by_user_id", "sent_by_user_id",
                                       "withdrawn_by_user_id", "preboarding_completed_by")),
        sa.CheckConstraint(f"status IN {STATES}", name="ck_offers_status"),
        sa.CheckConstraint(f"employment_type IN {EMPLOYMENT}", name="ck_offers_employment"),
        sa.CheckConstraint(f"pay_period IN {PERIODS}", name="ck_offers_period"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_offers_currency"),
        sa.CheckConstraint("base_salary > 0", name="ck_offers_salary"),
        sa.CheckConstraint("char_length(job_title) BETWEEN 1 AND 200", name="ck_offers_title"),
        sa.CheckConstraint("location IS NULL OR char_length(location) <= 200",
                           name="ck_offers_location"),
        sa.CheckConstraint("bonus IS NULL OR char_length(bonus) <= 1000", name="ck_offers_bonus"),
        sa.CheckConstraint("equity IS NULL OR char_length(equity) <= 1000", name="ck_offers_equity"),
        sa.CheckConstraint("benefits IS NULL OR char_length(benefits) <= 4000",
                           name="ck_offers_benefits"),
        sa.CheckConstraint("terms IS NULL OR char_length(terms) <= 20000", name="ck_offers_terms"),
        sa.CheckConstraint("probation_months IS NULL OR probation_months BETWEEN 0 AND 24",
                           name="ck_offers_probation"),
        sa.CheckConstraint("notice_period_days IS NULL OR notice_period_days BETWEEN 0 AND 365",
                           name="ck_offers_notice"),
        sa.CheckConstraint("valid_days BETWEEN 1 AND 60", name="ck_offers_valid_days"),
        sa.CheckConstraint("approval_note IS NULL OR char_length(approval_note) <= 1000",
                           name="ck_offers_approval_note"),
        sa.CheckConstraint("accepted_name IS NULL OR char_length(accepted_name) BETWEEN 1 AND 200",
                           name="ck_offers_accepted_name"),
        sa.CheckConstraint("decline_reason IS NULL OR char_length(decline_reason) <= 1000",
                           name="ck_offers_decline_reason"),
        sa.CheckConstraint("withdraw_reason IS NULL OR char_length(withdraw_reason) <= 1000",
                           name="ck_offers_withdraw_reason"),
        sa.CheckConstraint("preboarding_completed_at IS NULL OR status = 'accepted'",
                           name="ck_offers_preboarding_needs_acceptance"),
        sa.CheckConstraint("code_failures BETWEEN 0 AND 100", name="ck_offers_code_failures"),
    )
    op.create_index("ix_offers_enrolment", "offers", ["enrolment_id"])
    # At most one offer in play per application; a new one only after the last
    # was declined, expired or withdrawn.
    op.create_index("uq_offers_live_per_enrolment", "offers", ["enrolment_id"], unique=True,
                    postgresql_where=sa.text(
                        "status IN ('draft', 'pending_approval', 'approved', 'rejected', 'sent',"
                        " 'accepted')"))
    op.create_index("ix_offers_pending_approval", "offers", ["company_id", "submitted_at"],
                    postgresql_where=sa.text("status = 'pending_approval'"))
    op.create_index("ix_offers_sent_expiry", "offers", ["expires_at"],
                    postgresql_where=sa.text("status = 'sent'"))

    op.create_table(
        "offer_events",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("details", sa.dialects.postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_offer_events"),
        sa.ForeignKeyConstraint(["offer_id", "company_id"], ["offers.id", "offers.company_id"],
                                name="fk_offer_events_offer", ondelete="CASCADE"),
        _user("actor_user_id", "offer_events"),
        sa.CheckConstraint(f"action IN {EVENTS}", name="ck_offer_events_action"),
        sa.CheckConstraint("actor_type IN ('user', 'candidate', 'system')",
                           name="ck_offer_events_actor"),
    )
    op.create_index("ix_offer_events_offer", "offer_events", ["offer_id", "created_at"])

    op.create_table(
        "offer_codes",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        _ts("consumed_at"),
        sa.PrimaryKeyConstraint("id", name="pk_offer_codes"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], name="fk_offer_codes_offer",
                                ondelete="CASCADE"),
        sa.CheckConstraint("purpose IN ('accept', 'decline', 'documents')",
                           name="ck_offer_codes_purpose"),
        sa.CheckConstraint("attempts BETWEEN 0 AND 10", name="ck_offer_codes_attempts"),
    )
    op.create_index("ix_offer_codes_offer", "offer_codes", ["offer_id", "created_at"])

    # A preboarding session: opened with a one-time code, it — and not the
    # emailed link alone — is what lists and accepts a candidate's documents
    # (security review H1). Stored hashed; lives an hour.
    op.create_table(
        "offer_sessions",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_offer_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_offer_sessions_token_hash"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], name="fk_offer_sessions_offer",
                                ondelete="CASCADE"),
        sa.CheckConstraint("expires_at > created_at", name="ck_offer_sessions_expiry"),
    )

    op.add_column("enrolments", sa.Column("offer_outcome", sa.Text(), nullable=True))
    op.create_check_constraint("ck_enrolments_offer_outcome", "enrolments",
                               f"offer_outcome IS NULL OR offer_outcome IN {OUTCOMES}")

    for sql in (LIFECYCLE, LIFECYCLE_HOOK, EVENTS_APPEND_ONLY, EVENTS_HOOK, HIRE_FINAL,
                HIRE_FINAL_HOOK):
        op.execute(sql)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS enrolments_hire_undone_only_by_rejection ON enrolments")
    op.execute("DROP FUNCTION IF EXISTS enrolments_hire_undone_only_by_rejection()")
    op.execute("DROP TRIGGER IF EXISTS offer_events_append_only ON offer_events")
    op.execute("DROP FUNCTION IF EXISTS offer_events_append_only()")
    op.execute("DROP TRIGGER IF EXISTS offers_lifecycle ON offers")
    op.execute("DROP FUNCTION IF EXISTS offers_lifecycle()")
    op.drop_constraint("ck_enrolments_offer_outcome", "enrolments", type_="check")
    op.drop_column("enrolments", "offer_outcome")
    op.drop_table("offer_sessions")
    op.drop_index("ix_offer_codes_offer", table_name="offer_codes")
    op.drop_table("offer_codes")
    op.drop_index("ix_offer_events_offer", table_name="offer_events")
    op.drop_table("offer_events")
    for ix in ("ix_offers_sent_expiry", "ix_offers_pending_approval",
               "uq_offers_live_per_enrolment", "ix_offers_enrolment"):
        op.drop_index(ix, table_name="offers")
    op.drop_table("offers")
    op.drop_index("uq_offer_templates_name", table_name="offer_templates")
    op.drop_table("offer_templates")
