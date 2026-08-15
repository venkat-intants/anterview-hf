"""Behaviour pins for the shared HTTP observability layer (XS-01 / XS-05 / XS-06).

Each test states a property an operator or an auditor depends on, not an
implementation detail:

* an unhandled exception produces a **generic** body — the asyncpg leak in
  ``str(exc)`` carries the SQL *and its parameter values* (CWE-209);
* the same leak does not simply move into the log field: ``exc_msg`` is masked
  and length-capped at the call site, because the structlog PII processor is
  key-based and cannot see inside a value (DPDP §8);
* an unhandled exception is nevertheless **counted as a 500**, because the alert
  every team writes first is "5xx are climbing" and on three of four services
  that series structurally could not exist;
* the ``path`` label is the matched **route template**, and anything unmatched
  collapses to one constant, so an unauthenticated caller cannot mint time
  series (CWE-770).

Every test builds its own ``CollectorRegistry`` so the assertions are about the
sample values this test produced, and so the default registry is never polluted
by the suite.
"""

from __future__ import annotations

import pytest
import structlog
import structlog.testing
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from shared.http_observability import (
    _MAX_EXC_MSG_CHARS,
    _REDACTED,
    _SCAN_WINDOW_CHARS,
    _TRUNCATION_MARKER,
    UNKNOWN_METHOD,
    UNMATCHED_PATH,
    _safe_exc_message,
    install_http_observability,
)

_SERVICE = "test_service"

# A message shaped like the leak this layer exists to stop: asyncpg puts the
# failing statement and its bound parameters into str(exc), and those parameters
# are candidate PII (DPDP §8).
_DRIVER_LEAK = (
    'relation "sessions" does not exist: '
    "INSERT INTO users (email) VALUES ('candidate@example.com')"
)


def _count(
    registry: CollectorRegistry, path: str, status_code: str, method: str = "GET"
) -> float | None:
    return registry.get_sample_value(
        "http_requests_total",
        {
            "service": _SERVICE,
            "method": method,
            "path": path,
            "status_code": status_code,
        },
    )


def _latency_count(
    registry: CollectorRegistry, path: str, method: str = "GET"
) -> float | None:
    return registry.get_sample_value(
        "http_request_duration_seconds_count",
        {"service": _SERVICE, "method": method, "path": path},
    )


def _build_app(registry: CollectorRegistry) -> FastAPI:
    """A miniature service: a templated route, a plain route, and a boom route."""
    app = FastAPI()

    @app.get("/jobs/{job_id}")
    async def read_job(job_id: str) -> dict[str, str]:
        return {"job_id": job_id}

    @app.post("/jobs")
    async def create_job() -> dict[str, str]:
        return {"status": "created"}

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError(_DRIVER_LEAK)

    @app.get("/metrics")
    async def metrics() -> dict[str, str]:
        return {"scrape": "ok"}

    install_http_observability(app, service_name=_SERVICE, registry=registry)
    return app


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def client(registry: CollectorRegistry) -> TestClient:
    # raise_server_exceptions=False makes the test client behave like a real
    # deployment: the 500 the handler produced is what the caller receives.
    return TestClient(_build_app(registry), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# XS-06 — label cardinality is bounded by the route table, not by the caller
# ---------------------------------------------------------------------------


def test_path_label_is_the_route_template_not_the_concrete_id(
    client: TestClient, registry: CollectorRegistry
) -> None:
    """Two different job IDs must land on ONE series, keyed by the template."""
    client.get("/jobs/8f1b3d3e-0000-4000-8000-000000000001")
    client.get("/jobs/not-even-a-uuid")

    assert _count(registry, "/jobs/{job_id}", "200") == 2
    # The old regex-based normaliser only collapsed UUID-shaped segments, so the
    # second call above would have minted its own series.
    assert _count(registry, "/jobs/not-even-a-uuid", "200") is None


def test_unmatched_paths_collapse_to_one_constant_series(
    client: TestClient, registry: CollectorRegistry
) -> None:
    """CWE-770: a caller looping junk paths must not create a series per path."""
    for junk in ("/aaa", "/aab", "/aac"):
        assert client.get(junk).status_code == 404

    assert _count(registry, UNMATCHED_PATH, "404") == 3
    assert _count(registry, "/aaa", "404") is None


def test_wrong_method_on_a_real_route_keeps_the_template(
    client: TestClient, registry: CollectorRegistry
) -> None:
    """A 405 is a partial match, so it is attributed to the endpoint it hit."""
    assert client.get("/jobs").status_code == 405

    assert _count(registry, "/jobs", "405") == 1


def test_unknown_request_method_collapses_to_one_constant_label(
    client: TestClient, registry: CollectorRegistry
) -> None:
    """The method is as attacker-controlled as the path — same CWE-770 vector."""
    for verb in ("FROB", "BLORT"):
        assert client.request(verb, "/jobs/1").status_code == 405

    assert _count(registry, "/jobs/{job_id}", "405", method=UNKNOWN_METHOD) == 2
    assert _count(registry, "/jobs/{job_id}", "405", method="FROB") is None


# ---------------------------------------------------------------------------
# XS-01 — the 500 path: generic body out, MASKED detail to the log, counted
# ---------------------------------------------------------------------------


def test_unhandled_exception_returns_a_generic_body(client: TestClient) -> None:
    response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    body = response.text
    assert _DRIVER_LEAK not in body
    assert "RuntimeError" not in body
    assert "Traceback" not in body
    assert "candidate@example.com" not in body


def test_unhandled_exception_is_counted_as_a_500(
    client: TestClient, registry: CollectorRegistry
) -> None:
    """Without this, `rate(http_requests_total{status_code=~"5.."})` can never fire."""
    client.get("/boom")

    assert _count(registry, "/boom", "500") == 1
    assert _latency_count(registry, "/boom") == 1


def test_unhandled_exception_is_counted_exactly_once(
    client: TestClient, registry: CollectorRegistry
) -> None:
    """Middleware and exception handler both record; the scope flag deduplicates."""
    client.get("/boom")
    client.get("/boom")

    assert _count(registry, "/boom", "500") == 2


def test_unhandled_exception_detail_reaches_the_log_without_the_pii(
    client: TestClient,
) -> None:
    """The operator keeps the diagnosis; the candidate's address does not travel.

    INVERTED. This test previously pinned the opposite — it asserted
    ``exc_msg == _DRIVER_LEAK``, i.e. that a candidate e-mail address reached the
    log record, and called that "the operator must lose nothing that the client
    was denied". The module docstring justified it by pointing at the structlog
    PII processor, but ``shared/observability/pii.py`` matches on KEY NAME and
    ``exc_msg`` is not one of those keys, so nothing was redacting it. Denying
    the SQL to the client and then writing it to the log stream relocates a DPDP
    §8 problem rather than fixing it.
    """
    with structlog.testing.capture_logs() as logs:
        client.get("/boom")

    events = [entry for entry in logs if entry["event"] == "http.unhandled_exception"]
    assert len(events) == 1
    event = events[0]

    # The half an operator needs: what broke, and where.
    assert event["exc_type"] == "RuntimeError"
    assert event["path"] == "/boom"
    assert event["service"] == _SERVICE
    assert event["log_level"] == "error"
    # Double-quoted identifiers are the diagnosis and must survive intact.
    assert 'relation "sessions" does not exist' in event["exc_msg"]

    # The half that must not travel.
    assert "candidate@example.com" not in event["exc_msg"]
    assert _REDACTED in event["exc_msg"]
    # Belt and braces: no other field on the record smuggled it either.
    assert "candidate@example.com" not in repr(event)


def test_log_record_is_not_saved_by_the_structlog_pii_processor(
    client: TestClient,
) -> None:
    """The masking must be at the call site, because the processor cannot help.

    ``redact_pii_processor`` pops known PII *keys* off the event dict. Run it
    over the record this handler produces and it changes nothing — which is
    exactly why ``exc_msg`` had to be scrubbed before it was ever bound.
    """
    from shared.observability.pii import redact_pii_processor

    with structlog.testing.capture_logs() as logs:
        client.get("/boom")

    event = next(e for e in logs if e["event"] == "http.unhandled_exception")
    assert "exc_msg" in redact_pii_processor(None, "error", dict(event))


@pytest.mark.parametrize(
    ("raw", "must_go", "must_stay"),
    [
        # asyncpg renders bound parameters as single-quoted literals.
        (
            "INSERT INTO users (email) VALUES ('candidate@example.com')",
            "candidate@example.com",
            "INSERT INTO users",
        ),
        # A unique-violation DETAIL leaves the offending value UNQUOTED, so the
        # literal rule alone would miss it. The column name stays: it is what
        # makes the error actionable.
        (
            'duplicate key value violates unique constraint "users_email_key"\n'
            "DETAIL:  Key (email)=(ravi@example.com) already exists.",
            "ravi@example.com",
            '"users_email_key"',
        ),
        # Non-e-mail PII in the same unquoted position: a phone number.
        (
            "DETAIL:  Key (phone)=(9876543210) already exists.",
            "9876543210",
            "(phone)",
        ),
        # A name has no lexical shape of its own — the =(...) rule is what
        # catches it.
        (
            "DETAIL:  Key (full_name)=(Ravi Kumar) already exists.",
            "Ravi Kumar",
            "(full_name)",
        ),
        # Bare digit runs (roll number, Aadhaar) outside any delimiter.
        (
            "no row for candidate 200412345678 in cohort 7",
            "200412345678",
            "cohort 7",
        ),
    ],
)
def test_value_shapes_are_masked_but_identifiers_survive(
    raw: str, must_go: str, must_stay: str
) -> None:
    scrubbed = _safe_exc_message(RuntimeError(raw))

    assert must_go not in scrubbed
    assert must_stay in scrubbed
    assert _REDACTED in scrubbed


@pytest.mark.parametrize(
    ("raw", "must_go", "must_stay"),
    [
        # The measured leak: the apostrophe in "candidate's" paired with the
        # opening quote of the literal, so the rule masked `'s stored name '` and
        # emitted the name that followed it.
        (
            "candidate's stored name 'Ravi Kumar' is invalid",
            "Ravi Kumar",
            "candidate's",
        ),
        (
            'relation "users" doesn\'t exist: INSERT INTO users VALUES (\'Ravi Kumar\')',
            "Ravi Kumar",
            "doesn't exist",
        ),
        (
            "can't bind parameter $1 = 'ravi@example.com'",
            "ravi@example.com",
            "can't bind parameter",
        ),
    ],
)
def test_a_prose_apostrophe_does_not_unpair_the_literal_rule(
    raw: str, must_go: str, must_stay: str
) -> None:
    """A contraction is not a delimiter, and misreading one shifts every pair."""
    scrubbed = _safe_exc_message(RuntimeError(raw))

    assert must_go not in scrubbed
    assert must_stay in scrubbed


def test_a_postgres_doubled_quote_escape_over_redacts_rather_than_leaks() -> None:
    """Postgres escapes a quote inside a literal by doubling it: ``'it''s'``.

    The pairwise rule reads that as two adjacent literals and masks both, which
    is over-redaction — the direction this module picks on purpose. What the pin
    is really for is the apostrophe parking added for prose contractions: ``''``
    has no word character between its two quotes, so the parking must not reach
    into escaped SQL and turn this case into a leak.
    """
    scrubbed = _safe_exc_message(RuntimeError("bad literal 'it''s Ravi'"))

    assert "Ravi" not in scrubbed
    assert "bad literal" in scrubbed


def test_an_address_whose_local_part_has_an_apostrophe_is_masked_whole() -> None:
    """Half an address is still personal data, and still identifies the person."""
    scrubbed = _safe_exc_message(RuntimeError("no user for o'brien@example.com"))

    assert scrubbed == f"no user for {_REDACTED}"


def test_a_literal_with_no_closing_quote_is_still_masked() -> None:
    """The scan window can cut a literal mid-value; our own cut must not expose it."""
    scrubbed = _safe_exc_message(RuntimeError("bound arg 1: 'Ravi Kumar"))

    assert "Ravi Kumar" not in scrubbed
    assert "bound arg 1" in scrubbed


def test_exception_text_is_length_capped() -> None:
    """str(exc) is unbounded; a 500 in a retry loop must not fill the log store."""
    scrubbed = _safe_exc_message(RuntimeError("boom " + "a" * 10_000))

    assert len(scrubbed) == _MAX_EXC_MSG_CHARS + len(_TRUNCATION_MARKER)
    assert scrubbed.endswith(_TRUNCATION_MARKER)


def test_nothing_past_the_scan_window_can_reach_the_log() -> None:
    """Masking runs before the cap, so it must not run over an unbounded string.

    The window makes the emitted text a prefix of a string that was *fully*
    masked: a huge literal early in the message collapses to nine characters, and
    the address sitting past the window is dropped outright rather than pulled
    into view unscanned.
    """
    raw = "'" + "x" * _SCAN_WINDOW_CHARS + "' candidate@example.com"

    scrubbed = _safe_exc_message(RuntimeError(raw))

    assert "candidate@example.com" not in scrubbed
    assert scrubbed.endswith(_TRUNCATION_MARKER)
    assert len(scrubbed) <= _MAX_EXC_MSG_CHARS + len(_TRUNCATION_MARKER)


def test_masking_happens_before_truncation() -> None:
    """Order matters: truncating first can leave half an address behind.

    With the cap applied first, an address straddling the boundary is cut into a
    fragment that is still personal data. Masking first removes it whole.
    """
    raw = "a" * (_MAX_EXC_MSG_CHARS - 20) + " candidate@example.com"

    scrubbed = _safe_exc_message(RuntimeError(raw))

    assert "candidate" not in scrubbed
    assert scrubbed.endswith(_REDACTED)


def test_an_exception_that_cannot_be_stringified_does_not_break_the_handler() -> None:
    """This handler is the last thing that can fail; a broken __str__ must not.

    If ``str(exc)`` raised here, Starlette would fall back to the bare ASGI 500
    whose body is the CWE-209 leak this module exists to replace.
    """

    class UnprintableError(RuntimeError):
        def __str__(self) -> str:
            raise ValueError("nope")

    assert _safe_exc_message(UnprintableError()) == "<unprintable UnprintableError>"


def test_exception_still_propagates_to_the_server(registry: CollectorRegistry) -> None:
    """Sentry and the test client must still see the original exception.

    Starlette's ServerErrorMiddleware re-raises after our handler returns; if it
    did not, an error tracker would go silent the day this layer was installed.
    """
    strict_client = TestClient(_build_app(registry), raise_server_exceptions=True)

    with pytest.raises(RuntimeError, match="does not exist"):
        strict_client.get("/boom")


def test_exception_from_an_outer_middleware_is_still_counted(
    registry: CollectorRegistry,
) -> None:
    """A middleware added after ours wraps ours, so ours never runs.

    The counter must still learn about the 500 (that is the whole point of the
    handler fallback), but the latency histogram must NOT gain a fabricated
    sample for a request that was never timed.
    """
    app = FastAPI()

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    install_http_observability(app, service_name=_SERVICE, registry=registry)

    @app.middleware("http")
    async def _explode(request: object, call_next: object) -> None:
        raise RuntimeError("outer middleware failure")

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/ok").status_code == 500

    assert _count(registry, UNMATCHED_PATH, "500") == 1
    assert _latency_count(registry, UNMATCHED_PATH) is None


def test_debug_mode_bypasses_the_handler_and_says_so(registry: CollectorRegistry) -> None:
    """The one way to install the CWE-209 fix and have it do nothing.

    ``ServerErrorMiddleware`` consults ``self.debug`` before it consults the
    registered 500 handler, so a debug-mode app still answers with the traceback
    page. No service sets ``debug`` today; this pins the warning that fires if
    one ever does, and pins that the metrics half keeps working regardless.
    """
    app = FastAPI(debug=True)

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError(_DRIVER_LEAK)

    with structlog.testing.capture_logs() as logs:
        install_http_observability(app, service_name=_SERVICE, registry=registry)

    assert [entry["event"] for entry in logs] == [
        "http_observability.debug_bypasses_exception_handler"
    ]

    TestClient(app, raise_server_exceptions=False).get("/boom")
    assert _count(registry, "/boom", "500") == 1


def test_install_is_quiet_when_debug_is_off(registry: CollectorRegistry) -> None:
    with structlog.testing.capture_logs() as logs:
        install_http_observability(FastAPI(), service_name=_SERVICE, registry=registry)

    assert logs == []


# ---------------------------------------------------------------------------
# XS-05 — the metrics themselves
# ---------------------------------------------------------------------------


def test_successful_request_records_count_and_latency(
    client: TestClient, registry: CollectorRegistry
) -> None:
    assert client.post("/jobs").status_code == 200

    assert _count(registry, "/jobs", "200", method="POST") == 1
    assert _latency_count(registry, "/jobs", method="POST") == 1


def test_latency_histogram_has_a_bucket_on_the_two_second_nfr(
    client: TestClient, registry: CollectorRegistry
) -> None:
    """p95 < 2s is the platform NFR; the SLO threshold needs its own boundary."""
    client.post("/jobs")

    under_nfr = registry.get_sample_value(
        "http_request_duration_seconds_bucket",
        {"service": _SERVICE, "method": "POST", "path": "/jobs", "le": "2.0"},
    )
    assert under_nfr == 1


def test_metrics_endpoint_excludes_itself(
    client: TestClient, registry: CollectorRegistry
) -> None:
    """A 15s scrape would otherwise be the busiest endpoint in every service."""
    client.get("/metrics")

    assert _count(registry, "/metrics", "200") is None


def test_metrics_land_on_the_supplied_registry_only(
    client: TestClient, registry: CollectorRegistry
) -> None:
    """admin_ops exposes a private registry; the shared metrics must reach it."""
    from prometheus_client import REGISTRY as DEFAULT_REGISTRY

    client.post("/jobs")

    assert _count(registry, "/jobs", "200", method="POST") == 1
    assert (
        DEFAULT_REGISTRY.get_sample_value(
            "http_requests_total",
            {
                "service": _SERVICE,
                "method": "POST",
                "path": "/jobs",
                "status_code": "200",
            },
        )
        is None
    )


def test_two_apps_can_share_one_registry(registry: CollectorRegistry) -> None:
    """Installing twice on one registry reuses the collectors instead of raising.

    prometheus_client refuses a duplicate metric name; a service that mounts a
    sub-app, or a test process that builds several apps, would otherwise crash at
    import time.
    """
    first = TestClient(_build_app(registry), raise_server_exceptions=False)
    second = TestClient(_build_app(registry), raise_server_exceptions=False)

    first.post("/jobs")
    second.post("/jobs")

    assert _count(registry, "/jobs", "200", method="POST") == 2


def test_conflicting_metric_name_fails_with_an_actionable_error(
    registry: CollectorRegistry,
) -> None:
    """A service that still defines its own http_requests_total gets told so."""
    from prometheus_client import Counter

    Counter("http_requests_total", "service-local copy", registry=registry)

    with pytest.raises(RuntimeError, match="already"):
        install_http_observability(FastAPI(), service_name=_SERVICE, registry=registry)


def test_non_http_scopes_pass_through_untouched(registry: CollectorRegistry) -> None:
    """Lifespan and websocket traffic must not be timed or counted.

    A websocket's "duration" is its session length, which would be
    indistinguishable from a very slow request in the same histogram — and
    interview_core is a websocket service.
    """
    app = _build_app(registry)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.close()

    # Entering the TestClient context runs the lifespan scope, so this exercises
    # both non-http scope types.
    with TestClient(app) as client, client.websocket_connect("/ws"):
        pass

    assert _count(registry, "/ws", "200") is None
    assert _latency_count(registry, "/ws") is None
