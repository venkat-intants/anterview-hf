"""E5 smoke test — bulk uploads processed in the background, against Postgres.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head
(it TRUNCATEs what it touches), and writes CVs to a temporary directory:

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_e_bulk.py
"""

from __future__ import annotations

import asyncio
import pathlib
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []

SCORE = {"overall": 7, "breakdown": {"skills": 7}, "strengths": ["x"], "concerns": ["y"],
         "recommendation": "consider", "summary": "ok"}


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


def tiny_pdf(line: str) -> bytes:
    """A minimal one-page PDF pypdf can extract text from."""
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(xref).encode() + b"\n%%EOF")
    return bytes(out)


async def main() -> None:  # noqa: PLR0915 — one linear script
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE applicants, companies, users, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, round_criteria, round_results,"
            " reconciliation_state, upload_batches, upload_items CASCADE"))

    cid, other_cid, hr, other_hr = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    req, closed, foreign = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with f() as db:
        for company, user, slug in ((cid, hr, "acme"), (other_cid, other_hr, "globex")):
            await db.execute(text(
                "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                " VALUES (:i,:s,:s,true,:n,:n)"), {"i": company, "s": slug, "n": now})
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
                " is_active,notify_login_email,must_change_password,created_at,updated_at)"
                " VALUES (:i,:e,'Meera HR','x',:c,'en',true,false,false,:n,:n)"),
                {"i": user, "e": f"hr@{slug}.example.com", "c": company, "n": now})
        for rid, company, title, status in ((req, cid, "Support Engineer", "open"),
                                            (closed, cid, "Old Role", "closed"),
                                            (foreign, other_cid, "Welder", "open")):
            await db.execute(text(
                "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
                " created_at,updated_at) VALUES (:i,:c,:t,'mid',:s,false,:n,:n)"),
                {"i": rid, "c": company, "t": title, "s": status, "n": now})
        await db.commit()

    from app.config import settings

    store = pathlib.Path(tempfile.mkdtemp(prefix="intants-smoke-bulk-"))
    settings.storage_local_dir = str(store)
    settings.s3_access_key_id = ""
    settings.app_env = "development"

    import app.reconciliation as rec
    from app.bulk_ingest import GAVE_UP, INGEST_BATCH, MAX_INGEST_ATTEMPTS, UNREADABLE, ingest_pass
    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    who = {"ctx": (hr, cid)}
    app.dependency_overrides[get_hr_company] = lambda: who["ctx"]
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    def pdf_file(name: str, body: bytes, ctype: str = "application/pdf") -> tuple:
        return ("files", (name, body, ctype))

    # ── 1. The request only stores ─────────────────────────────────────────
    batch = [
        pdf_file("Anita_Rao.pdf", tiny_pdf("Anita Rao anita@example.com Support")),
        pdf_file("Bala_K.pdf", tiny_pdf("Bala K bala@example.com Support")),
        pdf_file("Chen_L.pdf", tiny_pdf("Chen L chen@example.com Support")),
        pdf_file("corrupt.pdf", b"%PDF-1.4 this is not really a pdf"),
        pdf_file("notes.txt", b"hello", "text/plain"),
        pdf_file("huge.pdf", tiny_pdf("big") + b"0" * (5 * 1024 * 1024)),
    ]
    r = await ac.post("/hr/applicants/bulk", files=batch, data={"requisition_id": str(req)})
    body = r.json() if r.status_code == 202 else {}
    check("the upload is accepted straight away", r.status_code == 202, f"{r.status_code} {r.text[:200]}")
    check("…with the stored files queued and each refused one named",
          body.get("accepted") == 4 and body.get("failed_count") == 2
          and {x["error"] for x in body.get("failed", [])} == {"Not a PDF.", "Over 5 MB."},
          str(body))
    batch_id = body.get("batch_id")
    async with f() as db:
        n_applicants = await db.scalar(text("SELECT count(*) FROM applicants"))
        items = {x["filename"]: dict(x) for x in (await db.execute(text(
            "SELECT filename, status, s3_key, company_id FROM upload_items WHERE batch_id = :b"),
            {"b": batch_id})).mappings().all()}
    check("nothing was read or created inside the request", n_applicants == 0, str(n_applicants))
    check("every file has a row: four waiting, two failed",
          sorted(i["status"] for i in items.values()) == ["failed", "failed", "stored", "stored",
                                                           "stored", "stored"], str(items))
    check("the stored bytes are really on disk", len(list(store.rglob("*.pdf"))) == 4,
          str(list(store.rglob("*.pdf"))))

    p = (await ac.get(f"/hr/uploads/{batch_id}")).json()
    check("progress shows four waiting, none added yet",
          p.get("queued") == 4 and p.get("created") == 0 and p.get("failed") == 2
          and p.get("finished") is False, str(p))

    # ── 2. The background pass reads, files and records the basis ──────────
    async with f() as db:
        result = rec.PassResult()
        await ingest_pass(db, result)
    async with f() as db:
        people = (await db.execute(text(
            "SELECT a.id, a.company_id, a.pending_enrichment, a.upload_batch_id, a.resume_s3_key,"
            "       e.requisition_id, e.applied_resume_s3_key"
            "  FROM applicants a JOIN enrolments e ON e.applicant_id = a.id"))).mappings().all()
        corrupt = (await db.execute(text(
            "SELECT status, error FROM upload_items WHERE filename = 'corrupt.pdf'"))).mappings().first()
        basis = (await db.execute(text(
            "SELECT user_id, purpose, evidence FROM dpdp_consent_ledger"
            " WHERE consent_type = 'hr_collected_application'"))).mappings().all()
    check("the pass turned three readable CVs into applicants", result.ingested == 3
          and len(people) == 3, f"ingested={result.ingested} people={len(people)}")
    check("…each filed under the opening the upload named, in this company",
          all(x["requisition_id"] == req and x["company_id"] == cid for x in people), str(people))
    check("…each tied to its batch, waiting to be scored, with its CV pinned",
          all(str(x["upload_batch_id"]) == batch_id and x["pending_enrichment"]
              and x["applied_resume_s3_key"] == x["resume_s3_key"] for x in people), str(people))
    check("an unreadable PDF fails for good, with a reason HR can act on",
          corrupt is not None and corrupt["status"] == "failed" and corrupt["error"] == UNREADABLE,
          str(corrupt))
    check("a 'collected by HR' basis is recorded per applicant, against the uploader",
          len(basis) == 3 and all(b["user_id"] == hr for b in basis)
          and len({b["purpose"] for b in basis}) == 3, str(basis))
    check("…with no personal data in the evidence",
          all("anita" not in str(b["evidence"]).lower() for b in basis), str(basis))

    p = (await ac.get(f"/hr/uploads/{batch_id}")).json()
    check("progress: all read, three being scored, three failed, not finished",
          p.get("queued") == 0 and p.get("created") == 3 and p.get("failed") == 3
          and p.get("being_scored") == 3 and p.get("finished") is False
          and len(p.get("failures", [])) == 3, str(p))

    # ── 3. Scoring finishes the batch, and the uploader is told ────────────
    async def _score(**_: object) -> dict:
        return {**SCORE, "candidate_name": "Real Name"}

    rec.score_resume_remote = _score  # type: ignore[assignment]
    async with f() as db:
        await rec._score_pass(db, rec.PassResult())
    p = (await ac.get(f"/hr/uploads/{batch_id}")).json()
    async with f() as db:
        note = (await db.execute(text(
            "SELECT body FROM notifications WHERE user_id = :u AND kind = 'bulk_upload'"),
            {"u": hr})).mappings().first()
        status = await db.scalar(text("SELECT status FROM upload_batches WHERE id = :b"),
                                 {"b": batch_id})
    check("the batch is finished once everything is read and scored",
          p.get("finished") is True and p.get("being_scored") == 0 and status == "finished",
          f"{p} status={status}")
    check("the uploader is notified once, counting the files that failed",
          note is not None and "3 of 6" in note["body"] and "3 could not be read" in note["body"],
          str(note))

    listed = (await ac.get("/hr/uploads", params={"requisition_id": str(req)})).json()
    check("recent uploads for the opening include it", any(b["batch_id"] == batch_id for b in listed),
          str(listed)[:200])

    # ── 4. Refusals and isolation ──────────────────────────────────────────
    one = [pdf_file("x.pdf", tiny_pdf("x"))]
    r = await ac.post("/hr/applicants/bulk", files=one, data={"requisition_id": str(foreign)})
    check("another company's opening is not found", r.status_code == 404, str(r.status_code))
    r = await ac.post("/hr/applicants/bulk", files=one, data={"requisition_id": str(closed)})
    check("a closed opening takes no uploads", r.status_code == 409, str(r.status_code))
    r = await ac.post("/hr/applicants/bulk", files=one, data={"target_job_title": "Electrician"})
    check("a typed title is not an opening", r.status_code == 422, str(r.status_code))
    who["ctx"] = (other_hr, other_cid)
    r = await ac.get(f"/hr/uploads/{batch_id}")
    check("another company cannot read this upload's progress", r.status_code == 404,
          str(r.status_code))
    who["ctx"] = (hr, cid)

    # ── 5. A storage failure is retried with backoff, then given up on ─────
    r = await ac.post("/hr/applicants/bulk", files=[pdf_file("Dev_P.pdf", tiny_pdf("Dev P"))],
                      data={"requisition_id": str(req)})
    retry_batch = r.json()["batch_id"]
    for path in store.rglob(f"*{retry_batch}*/*.pdf"):
        path.unlink()
    async with f() as db:
        await ingest_pass(db, rec.PassResult())
    async with f() as db:
        item = (await db.execute(text(
            "SELECT i.id, i.status, rs.attempts, rs.next_attempt_at FROM upload_items i"
            "  LEFT JOIN reconciliation_state rs ON rs.ref_id = i.id AND rs.kind = 'upload_item'"
            " WHERE i.batch_id = :b"), {"b": retry_batch})).mappings().first()
    check("a missing object goes back to the queue, with a backoff",
          item is not None and item["status"] == "stored" and item["attempts"] == 1
          and item["next_attempt_at"] is not None and item["next_attempt_at"] > now,
          str(item))
    async with f() as db:
        again = rec.PassResult()
        await ingest_pass(db, again)
    check("…and is not retried again before the backoff ends", again.ingested == 0 and
          again.failed == 0, str(again.as_dict()))
    async with f() as db:
        await db.execute(text(
            "UPDATE reconciliation_state SET attempts = :a, next_attempt_at = :p"
            " WHERE ref_id = :i AND kind = 'upload_item'"),
            {"a": MAX_INGEST_ATTEMPTS - 1, "p": now - timedelta(minutes=1), "i": item["id"]})
        await db.commit()
    async with f() as db:
        await ingest_pass(db, rec.PassResult())
    async with f() as db:
        final = (await db.execute(text("SELECT status, error FROM upload_items WHERE id = :i"),
                                  {"i": item["id"]})).mappings().first()
    check("after the last attempt it is failed, with a reason",
          final is not None and final["status"] == "failed" and final["error"] == GAVE_UP,
          str(final))

    # ── 6. A large batch drains across passes ──────────────────────────────
    many = [pdf_file(f"Candidate_{i:03d}.pdf", tiny_pdf(f"Candidate {i}")) for i in range(60)]
    r = await ac.post("/hr/applicants/bulk", files=many, data={"requisition_id": str(req)})
    big = r.json()
    check("sixty files are accepted in one request", r.status_code == 202
          and big.get("accepted") == 60, f"{r.status_code} {str(big)[:120]}")
    passes = []
    for _ in range(4):
        async with f() as db:
            res = rec.PassResult()
            await ingest_pass(db, res)
            passes.append(res.ingested)
    p = (await ac.get(f"/hr/uploads/{big['batch_id']}")).json()
    check("…and drain across passes, a bounded number at a time",
          passes[:3] == [INGEST_BATCH, INGEST_BATCH, 60 - 2 * INGEST_BATCH] and passes[3] == 0
          and p.get("created") == 60 and p.get("queued") == 0, f"{passes} {p}")

    await ac.aclose()
    app.dependency_overrides.clear()
    shutil.rmtree(store, ignore_errors=True)
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
