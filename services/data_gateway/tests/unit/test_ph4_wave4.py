"""PH4 Wave 4 — offers (A3), documents and preboarding (A4): the pieces that can
be tested on plain values, and the promises that hold by construction.

The database half (the offer lifecycle, the expiry clock, the review rules,
the completion gate, the append-only histories) is in
tests/integration/test_ph4_wave4_guarantees.py.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _calls(module: Any) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    return {
        (n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", ""))
        for n in ast.walk(tree) if isinstance(n, ast.Call)
    }


def _sql(module: Any) -> str:
    tree = ast.parse(inspect.getsource(module))
    return " ".join(n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)).upper()


# ===========================================================================
# Credentials
# ===========================================================================
def test_an_offer_token_is_long_random_and_stored_only_as_a_keyed_hash() -> None:
    from app.offer_security import hash_offer_token, mint_offer_token

    a, b = mint_offer_token(), mint_offer_token()
    assert a != b and len(a) >= 40
    assert hash_offer_token(a) == hash_offer_token(a) and hash_offer_token(a) != a
    assert len(hash_offer_token(a)) == 64


def test_a_code_is_six_digits_bound_to_its_offer_and_purpose() -> None:
    from app.offer_security import codes_match, hash_code, mint_code

    code = mint_code()
    assert len(code) == 6 and code.isdigit()
    stored = hash_code("offer-1", "accept", code)
    assert codes_match(stored, "offer-1", "accept", code)
    assert codes_match(stored, "offer-1", "accept", f" {code} ")  # pasted with spaces
    assert not codes_match(stored, "offer-1", "decline", code)  # a decline code cannot accept
    assert not codes_match(stored, "offer-2", "accept", code)   # nor open another offer


def test_a_session_token_hash_is_not_a_link_hash() -> None:
    """Domain-separated: a session token can never pass as the offer link, nor
    the link as a session, even were the same string presented as both."""
    from app.offer_security import hash_offer_token, hash_session_token, mint_offer_token

    raw = mint_offer_token()
    assert hash_session_token(raw) != hash_offer_token(raw)
    assert len(hash_session_token(raw)) == 64


def test_the_export_key_id_names_the_key_without_being_a_cheap_test_of_it() -> None:
    import hashlib

    from app.offer_security import _export_secret, export_key_id

    kid = export_key_id()
    assert len(kid) == 12 and kid == export_key_id()
    # Not a bare digest of the secret — which an offline guesser could match.
    assert kid != hashlib.sha256(_export_secret().encode()).hexdigest()[:12]


def test_the_export_signature_is_over_the_canonical_payload() -> None:
    from app.offer_security import canonical, export_key_id, sign_export, verify_export

    a = {"b": 1, "a": {"y": "ऑफ़र", "x": [1, 2]}}
    b = {"a": {"x": [1, 2], "y": "ऑफ़र"}, "b": 1}
    assert canonical(a) == canonical(b)  # key order does not matter
    sig = sign_export(a)
    assert verify_export(b, sig) and len(sig) == 64
    assert not verify_export({**a, "b": 2}, sig)
    assert len(export_key_id()) == 12


# ===========================================================================
# What gets in (decision D4-3)
# ===========================================================================
PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 50
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50


@pytest.mark.parametrize(("data", "kind"), [(PDF, "application/pdf"), (JPEG, "image/jpeg"),
                                            (PNG, "image/png")])
def test_only_pdf_jpeg_and_png_are_recognised_by_their_content(data: bytes, kind: str) -> None:
    from app.document_storage import check

    assert check(data, "anything.exe", max_bytes=10_000).content_type == kind


@pytest.mark.parametrize("data", [b"GIF89a....", b"MZ\x90\x00 an exe", b"<html>", b"PK\x03\x04"])
def test_anything_else_is_refused_whatever_it_is_called(data: bytes) -> None:
    from app.document_storage import DocumentRejectedError, check

    with pytest.raises(DocumentRejectedError, match="PDF, JPEG or PNG"):
        check(data, "passport.pdf", max_bytes=10_000)


@pytest.mark.parametrize("marker", [b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile",
                                    b"/RichMedia", b"/XFA"])
def test_a_pdf_that_can_run_or_carry_something_is_refused(marker: bytes) -> None:
    from app.document_storage import DocumentRejectedError, check

    with pytest.raises(DocumentRejectedError, match="scripts or embedded files"):
        check(PDF + b" << " + marker + b" (x) >>", "a.pdf", max_bytes=10_000)


@pytest.mark.parametrize("marker", [b"/J#61vaScript", b"/#4A#53", b"/Launc#68",
                                    b"/Embedded#46ile", b"/#58FA"])
def test_a_marker_spelled_with_name_escapes_is_still_refused(marker: bytes) -> None:
    from app.document_storage import DocumentRejectedError, check

    with pytest.raises(DocumentRejectedError):
        check(PDF.replace(b"%%EOF", b"<< " + marker + b" 1 0 R >>\n%%EOF"), "a.pdf",
              max_bytes=10_000)


def test_an_ordinary_pdf_with_an_open_action_is_accepted() -> None:
    from app.document_storage import check

    assert check(PDF + b" << /OpenAction [3 0 R /Fit] >>", "a.pdf", max_bytes=10_000)


def test_size_and_emptiness_are_refused_in_words() -> None:
    from app.document_storage import DocumentRejectedError, check

    with pytest.raises(DocumentRejectedError, match="empty"):
        check(b"", "a.pdf", max_bytes=10)
    with pytest.raises(DocumentRejectedError, match="larger than"):
        check(PDF + b"x" * 2_000, "a.pdf", max_bytes=1_000)


def test_a_file_name_carries_no_path_and_the_extension_of_its_content() -> None:
    from app.document_storage import check, safe_filename

    assert safe_filename("../../etc/passwd", "pdf") == "passwd.pdf"
    assert safe_filename("C:\\Users\\x\\My Passport!!.PDF", "pdf") == "My Passport.pdf"
    assert safe_filename('"; rm -rf', "png").endswith(".png")
    assert check(JPEG, "photo.png", max_bytes=10_000).safe_name == "photo.jpg"


def test_storage_keys_name_no_person() -> None:
    from app.document_storage import storage_key

    c, o, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert storage_key(c, o, d) == f"preboarding/{c}/{o}/{d}"


# ===========================================================================
# Offer fields
# ===========================================================================
def test_offer_fields_are_validated_in_words() -> None:
    from app.offers import OfferError, clean_fields

    clean = clean_fields({"base_salary": "1200000.456", "currency": "inr", "valid_days": 7,
                          "start_date": "2026-11-01", "job_title": "  Engineer "}, partial=False)
    assert clean["base_salary"] == Decimal("1200000.46") and clean["currency"] == "INR"
    assert clean["start_date"] == date(2026, 11, 1) and clean["job_title"] == "Engineer"
    for bad, words in (({"base_salary": 0}, "more than zero"),
                       ({"base_salary": "lots"}, "must be a number"),
                       ({"currency": "RUPEES", "base_salary": 1}, "three-letter"),
                       ({"pay_period": "weekly", "base_salary": 1}, "annual, monthly or hourly"),
                       ({"valid_days": 90, "base_salary": 1}, "1 to 60 days"),
                       ({"job_title": " ", "base_salary": 1}, "job title")):
        with pytest.raises(OfferError, match=words):
            clean_fields(bad, partial=False)
    with pytest.raises(OfferError, match="Set the base salary"):
        clean_fields({"currency": "INR"}, partial=False)


def test_the_candidate_view_has_no_approval_trail() -> None:
    from app.offers import candidate_out

    row = {"status": "sent", "job_title": "Engineer", "employment_type": "full_time",
           "start_date": date(2026, 11, 1), "location": "Pune", "base_salary": Decimal("1"),
           "currency": "INR", "pay_period": "annual", "bonus": None, "equity": None,
           "benefits": None, "terms": None, "probation_months": 6, "notice_period_days": 30,
           "expires_at": datetime(2026, 10, 1, tzinfo=UTC), "responded_at": None,
           "preboarding_completed_at": None, "approval_note": "secret note",
           "decided_by_user_id": uuid.uuid4(), "token_hash": "h"}
    out = candidate_out(row, "Acme")
    for private in ("approval_note", "decided_by_user_id", "token_hash", "created_by_user_id"):
        assert private not in out


# ===========================================================================
# The HRMS handoff carries only what an HRMS needs
# ===========================================================================
def test_the_hrms_payload_is_minimal() -> None:
    from app.preboarding import export_payload

    offer = {"id": uuid.uuid4(), "company_id": uuid.uuid4(), "company_name": "Acme",
             "candidate_name": "Asha", "candidate_email": "a@x.test", "job_title": "Engineer",
             "employment_type": "full_time", "start_date": date(2026, 11, 1), "location": "Pune",
             "base_salary": Decimal("1200000"), "currency": "INR", "pay_period": "annual",
             "probation_months": 6, "notice_period_days": 30,
             "responded_at": datetime(2026, 9, 25, tzinfo=UTC),
             "preboarding_completed_at": datetime(2026, 9, 30, tzinfo=UTC),
             "decline_reason": "x", "approval_note": "y", "accepted_name": "Asha R",
             "token_hash": "h"}
    payload = export_payload(offer, [{"type": "identity", "name": "Passport", "version": 1,
                                      "content_type": "application/pdf", "sha256": "a" * 64,
                                      "expires_on": None, "verified_at": None}],
                             prepared_at=datetime(2026, 10, 1, tzinfo=UTC), export_id=uuid.uuid4())
    flat = repr(payload)
    for never in ("approval_note", "decline_reason", "token", "storage_key", "score",
                  "scorecard", "review_note", "accepted_name"):
        assert never not in flat, never
    assert payload["offer"]["base_salary"] == "1200000.00"
    assert set(payload) == {"schema", "export_id", "prepared_at", "company", "candidate",
                            "offer", "documents", "preboarding_completed_at"}


# ===========================================================================
# What holds by construction
# ===========================================================================
def test_offers_and_documents_never_move_a_candidate_or_decide() -> None:
    import app.document_storage as ds
    import app.offers as offers
    import app.preboarding as pre

    for module in (offers, pre, ds):
        assert not (_calls(module) & {"record_transition", "record_round_move",
                                      "record_final_decision", "release_hold", "_hold"})
        sql = _sql(module)
        assert "UPDATE ENROLMENTS SET STATUS" not in sql
        assert "INSERT INTO STAGE_TRANSITIONS" not in sql
    # The only enrolment column an offer writes is its outcome, beside the decision.
    assert "UPDATE ENROLMENTS SET OFFER_OUTCOME" in _sql(offers)


def test_no_agent_or_model_can_reach_an_offer() -> None:
    src = "\n".join(inspect.getsource(m) for m in (
        __import__("app.offers", fromlist=["x"]), __import__("app.preboarding", fromlist=["x"])))
    for word in ("llm", "gemini", "groq", "anthropic", "shared.agents", "proposal"):
        assert word not in src.lower(), word
    agents = pathlib.Path(APP.parents[2] / "shared" / "agents")
    for path in agents.rglob("*.py"):
        text_ = path.read_text(encoding="utf-8")
        assert "/offers" not in text_ and "app.offers" not in text_, path


def test_every_route_sits_behind_its_audience_gate() -> None:
    source = (APP / "routers/offers.py").read_text(encoding="utf-8")
    gate = {"hr_router": "HrCtxDep", "admin_router": "SuperAdminCtxDep",
            "me_router": "CurrentUserDep", "public_router": "OfferTokenDep"}
    seen = 0
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for d in fn.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                router = getattr(d.func.value, "id", "")
                ann = " ".join(ast.unparse(a.annotation) for a in fn.args.args if a.annotation)
                assert gate[router] in ann, fn.name
                seen += 1
    assert seen >= 30


def test_documents_need_a_session_as_well_as_the_link() -> None:
    """Security review H1: every public route that lists or takes a document
    names the session dependency, and no public route serves a file."""
    import app.routers.offers as r

    source = inspect.getsource(r)
    assert 'Header(alias="X-Offer-Session")' in source
    tree = ast.parse(source)
    gated = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for d in fn.decorator_list:
            if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                    and getattr(d.func.value, "id", "") == "public_router" and d.args):
                continue
            path = d.args[0].value if isinstance(d.args[0], ast.Constant) else ""
            ann = " ".join(ast.unparse(a.annotation) for a in fn.args.args if a.annotation)
            if path == "/documents" or path.startswith("/documents/{"):
                assert "OfferSessionDep" in ann, fn.name
                gated += 1
    assert gated == 2
    assert not [rt.path for rt in r.public_router.routes if "download" in rt.path]


def test_the_session_is_checked_before_anything_is_listed() -> None:
    import asyncio
    from unittest.mock import AsyncMock, patch

    import app.offers as offers

    offer = {"id": uuid.uuid4(), "status": "accepted"}
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    with patch.object(offers, "by_token", AsyncMock(return_value=offer)):
        for session in (None, "", "x" * 201, "not-a-live-session"):
            with pytest.raises(offers.OfferError) as exc:
                asyncio.run(offers.with_documents_session(db, raw="t", session=session))
            assert exc.value.status_code == 401


def _code_db(failures_after: int) -> Any:
    from unittest.mock import AsyncMock, MagicMock

    db = AsyncMock()
    res = MagicMock()
    res.mappings.return_value.first.return_value = {
        "id": uuid.uuid4(), "code_hash": "0" * 64, "attempts": 0,
        "expires_at": datetime(2999, 1, 1, tzinfo=UTC)}
    db.execute = AsyncMock(return_value=res)
    db.scalar = AsyncMock(return_value=failures_after)
    return db


def test_a_locked_offer_refuses_every_code_until_hr_re_sends_it() -> None:
    import asyncio

    import app.offers as offers

    offer = {"id": uuid.uuid4(), "code_failures": offers.MAX_CODE_FAILURES}
    with pytest.raises(offers.OfferError) as exc:
        asyncio.run(offers._check_code(_code_db(0), offer, "documents", "123456"))
    assert exc.value.status_code == 423 and "locked" in exc.value.detail
    assert offers.MAX_CODE_FAILURES == 20


@pytest.mark.parametrize(("after", "told"), [(19, False), (20, True), (21, False)])
def test_a_wrong_code_counts_over_the_offer_s_life_and_hr_is_told_once(after: int,
                                                                       told: bool) -> None:
    import asyncio
    from unittest.mock import AsyncMock, patch

    import app.offers as offers

    offer = {"id": uuid.uuid4(), "code_failures": 0}
    tell = AsyncMock()
    with patch.object(offers, "_tell_hr", tell), pytest.raises(offers.OfferError) as exc:
        asyncio.run(offers._check_code(_code_db(after), offer, "accept", "000000"))
    assert exc.value.status_code == 422 and exc.value.keep  # the attempt is committed
    assert tell.await_count == (1 if told else 0)


def test_a_re_send_resets_the_lock_and_closes_every_session() -> None:
    import app.offers as offers

    src = inspect.getsource(offers.send)
    assert "code_failures = 0" in src and "DELETE FROM offer_sessions" in src


def test_the_offer_link_is_read_only_from_a_header_and_every_public_route_is_rate_limited() -> None:
    import app.routers.offers as r

    source = inspect.getsource(r)
    assert 'Header(alias="X-Offer-Token")' in source and "Query(" not in source
    for route in r.public_router.routes:
        deps = [getattr(d.call, "__qualname__", "") for d in route.dependant.dependencies]
        assert any("rate_limit" in q for q in deps), route.path


def test_compensation_is_served_only_to_hr_super_admins_and_the_candidate() -> None:
    """No interviewer, analytics or agent route returns an offer."""
    for path in APP.rglob("*.py"):
        if path.name in {"offers.py", "preboarding.py", "main.py"}:
            continue
        text_ = path.read_text(encoding="utf-8")
        assert "base_salary" not in text_, path


def test_new_tables_are_in_the_erasure_inventory_and_documents_leave_storage() -> None:
    inv = (APP.parents[1] / "admin_ops" / "app" / "erasure_executor.py").read_text(encoding="utf-8")
    for table in ("offer_templates", "offers", "offer_events", "offer_codes",
                  "document_requirements", "candidate_documents", "document_events",
                  "hrms_exports", "offer_sessions"):
        assert f'"{table}"' in inv, table
    assert "SELECT d.storage_key FROM candidate_documents d" in inv  # collected in step 1
    assert "DELETE FROM hrms_exports" in inv and "DELETE FROM offer_codes" in inv
    assert "DELETE FROM offer_sessions" in inv
    # An object no row names any more (a failed commit's) is found by prefix (L4).
    assert "keys_under" in inv and "preboarding/{company_id}/{offer_id}/" in inv


@pytest.mark.parametrize("lang", ["en", "hi", "te"])
def test_every_offer_email_speaks_the_candidate_s_language(lang: str) -> None:
    from app.email_templates import render

    for template, ctx in (
        ("offer_ready", {"company": "Acme", "offer_url": "https://x/offer#t", "expires": "1 Oct"}),
        ("offer_code", {"code": "042917", "purpose": "accept", "minutes": 10}),
        ("offer_code", {"code": "042917", "purpose": "decline", "minutes": 10}),
        ("offer_update", {"company": "Acme"}),
        ("document_update", {"document": "Passport <b>", "reason": "Blurry <i>"}),
        ("document_received", {"document": "Passport <b>"}),
    ):
        mail = render(template, lang, {"name": "Asha", "job_title": "Engineer", **ctx})
        assert mail.subject and "<b>" not in mail.html.replace("<strong>", "")
        if lang != "en":
            assert mail.subject != render(template, "en", {"job_title": "Engineer", **ctx}).subject


def test_a_code_email_never_carries_a_link() -> None:
    from app.email_templates import render

    mail = render("offer_code", "en", {"code": "042917", "purpose": "accept", "minutes": 10})
    assert "http" not in mail.html and "http" not in mail.text and "042917" in mail.text


def test_expired_offers_are_a_sweep_stage() -> None:
    import app.reminders as rem

    assert "_offer_expiry" in inspect.getsource(rem.run_once)


def test_a_hire_is_undone_only_by_a_rejection() -> None:
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from app.requisitions import StatusRefusedError, record_transition

    db = AsyncMock()
    res = MagicMock()
    res.first.return_value = ("hired", uuid.uuid4())
    db.execute = AsyncMock(return_value=res)
    with pytest.raises(StatusRefusedError, match="undone only by recording a rejection"):
        asyncio.run(record_transition(db, enrolment_id=uuid.uuid4(), company_id=uuid.uuid4(),
                                      to_status="shortlisted", actor_user_id=None,
                                      automated=False))
