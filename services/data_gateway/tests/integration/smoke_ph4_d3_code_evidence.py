#!/usr/bin/env python3
"""PH4-D3 end to end: two near-identical Python submissions producing exactly
one similarity signal, a third unrelated submission producing none, a
reference-solution match, tenant isolation and an audited source read over
HTTP, a human finding that moves no status, an analyser failure that still
leaves the attempt graded, and the DPDP-facing retention trail — through the
real API, against a real Postgres.

    cd services/data_gateway
    PYTHONPATH=".;../.." DATABASE_URL=postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_w5 \\
      DATABASE_SSL= python tests/integration/smoke_ph4_d3_code_evidence.py

Seeds its own companies; leaves them behind (unique slugs), like the other smokes.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("SMOKE_DATABASE_URL", "postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_w5")
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f" — {detail}" if not cond and detail else ""))


class _FakeTestResult:
    """Stands in for ``CodingTestResult`` — always passes, whatever the source
    or test case, so this smoke never depends on a real code-execution
    provider. Grading is not what PH4-D3 tests; the evidence pipeline is."""

    def __init__(self, index: int, is_sample: bool, weight: int) -> None:
        self.index = index
        self.is_sample = is_sample
        self.weight = weight
        self.passed = True
        self.timed_out = False
        self.error = None
        self.actual_output = "3"
        self.stderr = ""
        self.stdin = "1 2"
        self.expected_output = "3"


async def _fake_run_tests(
    *, language: str, source: str, test_cases: list[dict], time_limit_ms: int, include_hidden: bool,
) -> list[_FakeTestResult]:
    return [
        _FakeTestResult(i, bool(tc.get("is_sample")), int(tc.get("weight", 1)))
        for i, tc in enumerate(test_cases)
    ]


# ---------------------------------------------------------------------------
# Fixtures: A and B are the same program with every identifier renamed
# (structurally identical -> containment 1.0); C is a different approach
# entirely (0 shared fingerprints with either). All three clear the
# 50-token minimum. Verified empirically against the real winnowing
# implementation before being pinned here.
# ---------------------------------------------------------------------------
REFERENCE_SOLUTION = """def compute_sum(first_value, second_value):
    total = first_value + second_value
    return total


def main():
    raw = input().split()
    a = int(raw[0])
    b = int(raw[1])
    result = compute_sum(a, b)
    print(result)


main()
"""

SOURCE_A = """def add_numbers(x, y):
    result = x + y
    return result


def run():
    parts = input().split()
    n1 = int(parts[0])
    n2 = int(parts[1])
    output = add_numbers(n1, n2)
    print(output)


run()
"""

SOURCE_B = """def sum_values(p, q):
    outcome = p + q
    return outcome


def execute():
    tokens = input().split()
    v1 = int(tokens[0])
    v2 = int(tokens[1])
    answer = sum_values(v1, v2)
    print(answer)


execute()
"""

SOURCE_C = """class Calculator:
    def __init__(self):
        self.history = []

    def add(self, x, y):
        value = x + y
        self.history.append(value)
        return value


def start():
    data = input()
    pieces = data.split(" ")
    calc = Calculator()
    total = calc.add(int(pieces[0]), int(pieces[1]))
    print(str(total))


start()
"""


async def main() -> None:  # noqa: PLR0915 — one linear script, read top to bottom
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    tag = uuid.uuid4().hex[:8]
    cid = uuid.uuid4()
    hr_a = uuid.uuid4()
    interviewer = uuid.uuid4()

    async with factory() as db:
        await db.execute(
            text("INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                 " VALUES (:i,'Acme D3',:s,true,:n,:n)"),
            {"i": cid, "s": f"d3-{tag}", "n": now},
        )
        for uid, name, roles in (
            (hr_a, "Hema HR", ("hr_manager",)), (interviewer, "Ivy Interviewer", ("interviewer",)),
        ):
            await db.execute(
                text("INSERT INTO users (id,email,full_name,password_hash,company_id,"
                     " preferred_language,is_active,notify_login_email,must_change_password,"
                     " created_at,updated_at)"
                     " VALUES (:i,:e,:fn,'x',:c,'en',true,false,false,:n,:n)"),
                {"i": uid, "e": f"{uid.hex[:10]}@{tag}.test", "fn": name, "c": cid, "n": now},
            )
            for role in roles:
                await db.execute(
                    text("INSERT INTO user_roles (user_id, role_id, assigned_at)"
                         " SELECT :u, id, :n FROM roles WHERE name = :r"),
                    {"u": uid, "n": now, "r": role},
                )
        await db.commit()

    from shared.auth.base import User

    import app.routers.exam_take as exam_take_mod
    from app.code_evidence import analyse_attempt, analyse_pending, purge
    from app.config import settings
    from app.database import get_db_session
    from app.dependencies import get_current_user, get_hr_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with factory() as session:
            yield session

    acting = {"hr": hr_a, "company": cid}
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_hr_company] = lambda: (acting["hr"], acting["company"])
    exam_take_mod.run_tests = _fake_run_tests  # type: ignore[assignment]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://smoke") as c:
        print("\nPH4-D3 — a coding round with a reference solution, published")
        r = await c.post("/hr/exams", json={"title": "Coding screen", "kind": "coding",
                                            "allow_retake": True})
        exam = r.json()
        exam_id = exam["id"]
        check("HR creates an exam", r.status_code == 201, r.text[:200])
        r = await c.get(f"/hr/exams/{exam_id}/structure")
        struct = r.json()
        round_id = struct["rounds"][0]["id"]
        section_id = struct["rounds"][0]["sections"][0]["id"]
        r = await c.patch(f"/hr/exams/{exam_id}/rounds/{round_id}",
                          json={"time_limit_seconds": 600, "pass_threshold": 50})
        check("round configured", r.status_code == 200, r.text[:200])
        r = await c.post(
            f"/hr/exams/{exam_id}/sections/{section_id}/coding-questions",
            json={
                "prompt": "Read two integers and print their sum",
                "allowed_languages": ["python"],
                "reference_solution": REFERENCE_SOLUTION,
                "test_cases": [
                    {"stdin": "1 2", "expected_output": "3", "is_sample": True, "weight": 1},
                ],
                "time_limit_ms": 2000, "points": 100,
            },
        )
        check("HR adds a coding question with a reference solution", r.status_code == 201, r.text[:300])
        question_id = r.json()["id"]
        r = await c.patch(f"/hr/exams/{exam_id}/rounds/{round_id}", json={"status": "published"})
        check("HR publishes the round", r.json().get("status") == "published", r.text[:200])

        print("\nPH4-D3 — three candidates: A and B near-identical, C different")
        applicant_a, applicant_b, applicant_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with factory() as db:
            await db.execute(
                text("INSERT INTO applicants (id,company_id,full_name,target_job_title)"
                     " VALUES (:a,:c,'Asha','Engineer'), (:b,:c,'Bilal','Engineer'),"
                     "        (:cc,:c,'Chetan','Engineer')"),
                {"a": applicant_a, "b": applicant_b, "cc": applicant_c, "c": cid},
            )
            await db.commit()

        r = await c.post(f"/hr/exams/{exam_id}/assignments",
                         json={"applicant_ids": [str(applicant_a), str(applicant_b), str(applicant_c)]})
        assigned = {row["applicant_id"]: row for row in r.json()}
        check("all three applicants are assigned", r.status_code == 201 and len(assigned) == 3,
              r.text[:200])
        tokens = {
            aid: assigned[str(aid)]["magic_link"].split("#")[-1]
            for aid in (applicant_a, applicant_b, applicant_c)
        }

        attempts: dict[uuid.UUID, str] = {}
        for aid, source in ((applicant_a, SOURCE_A), (applicant_b, SOURCE_B), (applicant_c, SOURCE_C)):
            headers = {"X-Exam-Token": tokens[aid]}
            r = await c.post("/exam/start", headers=headers)
            attempt_id = r.json()["attempt_id"]
            r = await c.post(
                "/exam/submit", headers=headers,
                json={"attempt_id": attempt_id,
                      "submissions": {question_id: {"language": "python", "source": source}}},
            )
            check(f"candidate submits ({aid})", r.status_code == 200 and r.json()["status"] == "submitted",
                  r.text[:200])
            attempts[aid] = attempt_id

        original_scores = {}
        async with factory() as db:
            for aid, attempt_id in attempts.items():
                row = (await db.execute(
                    text("SELECT score_percent, status FROM exam_attempts WHERE id = :i"),
                    {"i": uuid.UUID(attempt_id)},
                )).first()
                original_scores[aid] = (row[0], row[1])

        print("\nPH4-D3 — the sweep analyses all three and compares them")
        # analyse_pending is GLOBAL and batched, by design: it is the
        # platform's background job, not scoped to one exam. On a database
        # with a backlog of other pending attempts -- which is what the full
        # test suite leaves behind -- one pass can fill its batch before it
        # reaches this run's three. The first version called it once and
        # checked `reports_written >= 3`, which OTHER attempts' reports could
        # satisfy, so the checks passed while this run's own work had not
        # been analysed at all -- and then pair_signals[0] hit an empty list.
        # So: sweep until THIS run's attempts are all reported, bounded, and
        # assert on them by id.
        own_ids = [uuid.UUID(a) for a in attempts.values()]
        own_reported = 0
        passes = 0
        for passes in range(1, 31):  # noqa: B007 -- read after the loop
            async with factory() as db:
                await analyse_pending(db)
                own_reported = await db.scalar(
                    text("SELECT count(DISTINCT attempt_id) FROM code_quality_reports"
                         " WHERE attempt_id = ANY(:ids)"),
                    {"ids": own_ids},
                ) or 0
            if own_reported == len(own_ids):
                break
        check("the sweep reached and reported all three of this run's attempts",
              own_reported == len(own_ids), f"{own_reported}/{len(own_ids)} after {passes} passes")
        async with factory() as db:
            own_fingerprints = await db.scalar(
                text("SELECT count(*) FROM code_fingerprints WHERE attempt_id = ANY(:ids)"),
                {"ids": own_ids},
            ) or 0
        check("…and fingerprinted all three", own_fingerprints == len(own_ids), str(own_fingerprints))

        r = await c.get(f"/hr/exams/{exam_id}/similarity")
        signals = r.json()
        check("similarity endpoint returns the signals", r.status_code == 200, r.text[:200])
        pair_signals = [s for s in signals if s["reference_kind"] == "submission"]
        reference_signals = [s for s in signals if s["reference_kind"] == "reference_solution"]
        check("exactly one submission-pair signal (A and B only)", len(pair_signals) == 1,
              str(pair_signals))
        check("at least one reference-solution signal", len(reference_signals) >= 1,
              str(reference_signals))
        if not pair_signals:
            # Fail with a reason, not an IndexError that stops the smoke.
            raise SystemExit("no submission-pair signal: nothing below can be checked")
        pair_attempt_ids = {pair_signals[0]["attempt_low_id"], pair_signals[0]["attempt_high_id"]}
        check("the pair signal is exactly (A, B), never C",
              pair_attempt_ids == {attempts[applicant_a], attempts[applicant_b]}, str(pair_attempt_ids))

        print("\nPH4-D3 — grading is untouched by analysis")
        async with factory() as db:
            for aid, attempt_id in attempts.items():
                row = (await db.execute(
                    text("SELECT score_percent, status FROM exam_attempts WHERE id = :i"),
                    {"i": uuid.UUID(attempt_id)},
                )).first()
                check(f"attempt {aid} score/status unchanged after the sweep",
                      (row[0], row[1]) == original_scores[aid], f"{row} vs {original_scores[aid]}")

        print("\nPH4-D3 — evidence, tenant isolation, and audited source reads")
        aid_a = attempts[applicant_a]
        r = await c.get(f"/hr/exams/{exam_id}/attempts/{aid_a}/code-evidence")
        evidence = r.json()
        check("evidence tab returns reports, test results and an integrity summary",
              r.status_code == 200 and evidence["reports"] and "test_results" in evidence
              and "integrity" in evidence, r.text[:300])
        check("coverage is always unavailable, never silently omitted",
              all(rep["coverage"]["available"] is False for rep in evidence["reports"]),
              str(evidence["reports"]))

        # Security review D3 M2: the attempt page's OTHER call, /breakdown,
        # sent every test's stdout/stderr and the hidden cases' inputs and
        # expected outputs, unaudited. It now carries only what the page shows.
        r = await c.get(f"/hr/exams/{exam_id}/attempts/{aid_a}/breakdown")
        coding = r.json().get("coding", {}) if r.status_code == 200 else {}
        leaked = [k for k in ("actual_output", "stderr", "stdin", "expected_output")
                  if f'"{k}"' in r.text]
        check("the breakdown carries each coding score and pass/fail, never program output",
              r.status_code == 200 and coding and not leaked
              and all("points" in v for v in coding.values()), f"leaked={leaked} {r.text[:200]}")

        r = await c.get(f"/hr/exams/{exam_id}/attempts/{aid_a}/code/{question_id}")
        check("HR reads A's source", r.status_code == 200 and r.json()["source"] == SOURCE_A, r.text[:200])
        async with factory() as db:
            audit_count = await db.scalar(
                text("SELECT count(*) FROM audit_log WHERE action = 'code_evidence.source_viewed'"
                     " AND resource_id = :a"),
                {"a": uuid.UUID(aid_a)},
            )
        check("reading source wrote an audit row", int(audit_count or 0) >= 1, str(audit_count))

        other_cid = uuid.uuid4()
        async with factory() as db:
            await db.execute(
                text("INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                     " VALUES (:i,'Beta D3',:s,true,:n,:n)"),
                {"i": other_cid, "s": f"d3o-{tag}", "n": now},
            )
            await db.commit()
        other_hr = uuid.uuid4()
        acting["hr"] = other_hr
        acting["company"] = other_cid
        r = await c.get(f"/hr/exams/{exam_id}/attempts/{aid_a}/code-evidence")
        check("another company's HR gets 404 on the evidence tab", r.status_code == 404, r.text[:200])
        acting["hr"] = hr_a
        acting["company"] = cid

        del app.dependency_overrides[get_hr_company]
        app.dependency_overrides[get_current_user] = lambda: User(
            user_id=str(interviewer), full_name="Ivy", email="ivy@x.test", roles=["interviewer"])
        r = await c.get(f"/hr/exams/{exam_id}/attempts/{aid_a}/code-evidence")
        check("an interviewer gets 403 on the evidence tab", r.status_code == 403, r.text[:200])
        del app.dependency_overrides[get_current_user]
        app.dependency_overrides[get_hr_company] = lambda: (acting["hr"], acting["company"])

        print("\nPH4-D3 — a confirmed finding changes no status")
        signal_id = pair_signals[0]["id"]
        r = await c.post(
            "/hr/code-integrity-findings",
            json={
                "attempt_id": aid_a, "coding_question_id": question_id, "outcome": "confirmed",
                "rationale": "Manually compared both submissions and they are line-for-line identical "
                             "apart from variable names.",
                "signal_id": signal_id,
            },
        )
        check("HR records a confirmed finding", r.status_code == 201, r.text[:300])
        async with factory() as db:
            row = (await db.execute(
                text("SELECT score_percent, status FROM exam_attempts WHERE id = :i"),
                {"i": uuid.UUID(aid_a)},
            )).first()
        check("recording the finding changed no score or status",
              (row[0], row[1]) == original_scores[applicant_a], str(row))
        # The applicant/enrolment path is untouched — there is no enrolment
        # here at all (a hand-assigned exam), which is itself the proof: the
        # finding endpoint has no code path that could reach one.

        print("\nPH4-D3 — an injected analyser failure still leaves the attempt graded")
        applicant_e = uuid.uuid4()
        async with factory() as db:
            await db.execute(
                text("INSERT INTO applicants (id,company_id,full_name,target_job_title)"
                     " VALUES (:i,:c,'Esha','Engineer')"),
                {"i": applicant_e, "c": cid},
            )
            await db.commit()
        r = await c.post(f"/hr/exams/{exam_id}/assignments", json={"applicant_ids": [str(applicant_e)]})
        token_e = r.json()[0]["magic_link"].split("#")[-1]
        headers_e = {"X-Exam-Token": token_e}
        r = await c.post("/exam/start", headers=headers_e)
        attempt_e = r.json()["attempt_id"]
        r = await c.post(
            "/exam/submit", headers=headers_e,
            json={"attempt_id": attempt_e,
                  "submissions": {question_id: {"language": "python", "source": SOURCE_C}}},
        )
        check("E submits", r.status_code == 200 and r.json()["status"] == "submitted", r.text[:200])

        import app.code_evidence as code_evidence_mod

        async def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("injected analyser failure")

        real_run_isolated = code_evidence_mod.run_isolated
        code_evidence_mod.run_isolated = _boom  # type: ignore[assignment]
        try:
            async with factory() as db:
                result = await analyse_attempt(db, company_id=cid, attempt_id=uuid.UUID(attempt_e))
        finally:
            code_evidence_mod.run_isolated = real_run_isolated
        check("the failed analysis still wrote a report", result.reports_written == 1, str(result))
        async with factory() as db:
            report_status = await db.scalar(
                text("SELECT status FROM code_quality_reports WHERE attempt_id = :a"),
                {"a": uuid.UUID(attempt_e)},
            )
            attempt_row = (await db.execute(
                text("SELECT status, score_percent FROM exam_attempts WHERE id = :i"),
                {"i": uuid.UUID(attempt_e)},
            )).first()
        check("the report is stored as failed", report_status == "failed", str(report_status))
        check("E's attempt is still submitted and graded despite the failure",
              attempt_row[0] == "submitted" and attempt_row[1] is not None, str(attempt_row))

        print("\nPH4-D3 — analysis disabled is a documented no-op")
        applicant_f = uuid.uuid4()
        async with factory() as db:
            await db.execute(
                text("INSERT INTO applicants (id,company_id,full_name,target_job_title)"
                     " VALUES (:i,:c,'Farah','Engineer')"),
                {"i": applicant_f, "c": cid},
            )
            await db.commit()
        r = await c.post(f"/hr/exams/{exam_id}/assignments", json={"applicant_ids": [str(applicant_f)]})
        token_f = r.json()[0]["magic_link"].split("#")[-1]
        headers_f = {"X-Exam-Token": token_f}
        r = await c.post("/exam/start", headers=headers_f)
        attempt_f = r.json()["attempt_id"]
        r = await c.post(
            "/exam/submit", headers=headers_f,
            json={"attempt_id": attempt_f,
                  "submissions": {question_id: {"language": "python", "source": SOURCE_A}}},
        )
        check("F submits", r.status_code == 200 and r.json()["status"] == "submitted", r.text[:200])
        original_enabled = settings.code_analysis_enabled
        settings.code_analysis_enabled = False
        try:
            async with factory() as db:
                disabled_result = await analyse_pending(db)
        finally:
            settings.code_analysis_enabled = original_enabled
        check("a disabled sweep writes nothing", disabled_result.reports_written == 0
              and disabled_result.attempts_scanned == 0, str(disabled_result))
        async with factory() as db:
            no_report = await db.scalar(
                text("SELECT count(*) FROM code_quality_reports WHERE attempt_id = :a"),
                {"a": uuid.UUID(attempt_f)},
            )
        check("...and no report exists for F while disabled", int(no_report or 0) == 0, str(no_report))

        print("\nPH4-D3 — retention redacts source, deletes evidence, keeps the score")
        async with factory() as db:
            dry = await purge(db, retention_days=0, dry_run=True)
            await db.commit()
        check("a retention dry run reports candidates without redacting", dry >= 1, str(dry))
        async with factory() as db:
            still_there = await db.scalar(
                text("SELECT (answers -> 'coding' -> :q ->> 'source') FROM exam_attempts WHERE id = :i"),
                {"i": uuid.UUID(aid_a), "q": question_id},
            )
        check("...and dry run changed nothing", still_there == SOURCE_A, "source was redacted early")

        async with factory() as db:
            purged = await purge(db, retention_days=0, dry_run=False)
            await db.commit()
        check("the real run redacts", purged >= 1, str(purged))
        async with factory() as db:
            row = (await db.execute(
                text("SELECT answers, code_redacted_at, score_percent, status"
                     "  FROM exam_attempts WHERE id = :i"),
                {"i": uuid.UUID(aid_a)},
            )).first()
            reports_left = await db.scalar(
                text("SELECT count(*) FROM code_quality_reports WHERE attempt_id = :a"),
                {"a": uuid.UUID(aid_a)},
            )
            fingerprints_left = await db.scalar(
                text("SELECT count(*) FROM code_fingerprints WHERE attempt_id = :a"),
                {"a": uuid.UUID(aid_a)},
            )
            signals_left = await db.scalar(
                text("SELECT count(*) FROM code_similarity_signals WHERE attempt_low_id = :a"
                     " OR attempt_high_id = :a"),
                {"a": uuid.UUID(aid_a)},
            )
            finding_rationale = await db.scalar(
                text("SELECT rationale FROM code_integrity_findings WHERE attempt_id = :a"),
                {"a": uuid.UUID(aid_a)},
            )
        check("source is redacted", row.answers["coding"][question_id]["source"] is None, str(row.answers))
        check("code_redacted_at is stamped", row.code_redacted_at is not None, str(row))
        check("the score survives redaction", (row.score_percent, row.status) == original_scores[applicant_a],
              str((row.score_percent, row.status)))
        check("reports were deleted", int(reports_left or 0) == 0, str(reports_left))
        check("fingerprints were deleted", int(fingerprints_left or 0) == 0, str(fingerprints_left))
        check("signals were deleted", int(signals_left or 0) == 0, str(signals_left))
        check("the finding's rationale is redacted", finding_rationale == "[redacted]", str(finding_rationale))

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
