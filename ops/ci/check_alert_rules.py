#!/usr/bin/env python3
"""Structural gate for ``ops/alerts/`` — the alerting config committed for DG-6.

Why this script exists
----------------------
Code review 2026-08-07 finding **DG-6**: ``rate_limit_check_skipped_total``
existed and nothing consumed it. ``ops/alerts/`` is the consumer. But alerting
config that lives in a repository and is never exercised rots in a specific,
silent way — someone renames the counter in ``rate_limit.py``, the PromQL still
parses perfectly, the alert simply never fires again, and the dashboard stays
green. That is the *same* class of defect as the one this directory closes, one
layer up.

So CI checks two things a linter cannot:

1. **The rules are well-formed** — YAML parses, every rule is a ``record`` or an
   ``alert``, every alert carries ``for``, a ``severity`` label and enough
   annotation for the person it wakes up. ``promtool check rules`` is the
   authoritative validator (it speaks PromQL; this does not) and the command is
   in ``ops/alerts/README.md`` — but promtool needs a Prometheus binary, and a
   gate that only runs when someone remembers to run it is not a gate.

2. **Every metric the rules reference still exists in the service source.** The
   metric names are harvested from ``Counter``/``Histogram``/``Gauge``
   constructor calls under ``services/*/app`` and ``shared/``, so renaming a
   counter turns this red instead of silently retiring an alert.

It also checks the scrape config, because on this platform the scrape is part of
the alert: since security finding M-6, ``/metrics`` requires
``Authorization: Bearer $METRICS_TOKEN``, so a scrape config without credentials
401s on every target and every rule evaluates over nothing. And any ``job="..."``
matcher used in a rule must name a job the scrape config actually defines —
otherwise the expression is valid, permanently empty, and looks healthy.

Run locally exactly as CI runs it::

    python ops/ci/check_alert_rules.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - environment problem, not a finding
    sys.exit("FAIL: PyYAML is required. pip install pyyaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
ALERTS_DIR = REPO_ROOT / "ops" / "alerts"
SCRAPE_EXAMPLE = ALERTS_DIR / "prometheus-scrape.example.yml"

# Where application metrics are DEFINED. Deliberately narrow: `services/*/app`
# and `shared/` only, never `services/*/.venv`, which contains prometheus_client
# itself and would poison the name set with its own examples.
METRIC_SOURCE_GLOBS = ("services/*/app/**/*.py", "shared/**/*.py")

_METRIC_DEF_RE = re.compile(
    r"\b(?:Counter|Gauge|Histogram|Summary|Info|Enum)\(\s*[\"']([a-zA-Z_:][a-zA-Z0-9_:]*)[\"']"
)

# PromQL identifiers that are syntax, not metric names. Anything left over after
# these are removed has to be a real series.
_PROMQL_WORDS = frozenset(
    """
    abs absent absent_over_time avg avg_over_time bool bottomk by ceil changes
    clamp clamp_max clamp_min count count_over_time count_values delta deriv exp
    floor group group_left group_right histogram_quantile idelta ignoring increase
    irate last_over_time label_join label_replace ln log10 log2 max max_over_time
    min min_over_time offset on predict_linear quantile quantile_over_time rate
    resets round scalar sgn sort sort_desc sqrt stddev stddev_over_time stdvar
    sum sum_over_time time timestamp topk unless vector without and or
    """.split()
)

# `up` is synthetic — Prometheus generates it per target, so it will never appear
# in a Counter(...) call. It is the one legitimate exception to rule 2.
_SYNTHETIC_METRICS = frozenset({"up", "scrape_duration_seconds", "scrape_samples_scraped"})

# Histogram/summary suffixes: `foo_bucket` is exported from a Histogram named
# `foo`, so strip these before looking a name up.
_DERIVED_SUFFIXES = ("_bucket", "_count", "_sum")

_LABEL_LIST_RE = re.compile(
    r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)"
)
_IDENT_RE = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_JOB_MATCHER_RE = re.compile(r'job\s*=~?\s*"([^"]+)"')


def known_metric_names() -> set[str]:
    names: set[str] = set()
    for pattern in METRIC_SOURCE_GLOBS:
        for path in REPO_ROOT.glob(pattern):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            names.update(_METRIC_DEF_RE.findall(path.read_text(encoding="utf-8")))
    return names


def metrics_referenced(expr: str) -> set[str]:
    """Best-effort metric extraction from a PromQL expression.

    Not a parser. Label matchers ``{...}``, range selectors ``[...]`` and
    aggregation label lists ``by (...)`` are stripped first, because every
    identifier inside them is a LABEL name and would otherwise be mistaken for a
    metric. What survives is either PromQL syntax or a series name.
    """
    stripped = re.sub(r"\{[^}]*\}", " ", expr)
    stripped = re.sub(r"\[[^\]]*\]", " ", stripped)
    stripped = _LABEL_LIST_RE.sub(" ", stripped)
    return {
        token
        for token in _IDENT_RE.findall(stripped)
        if token not in _PROMQL_WORDS and not token.isdigit()
    }


def _resolve(name: str, known: set[str]) -> bool:
    if name in known or name in _SYNTHETIC_METRICS:
        return True
    for suffix in _DERIVED_SUFFIXES:
        if name.endswith(suffix) and name[: -len(suffix)] in known:
            return True
    return False


def check_rule_files(known: set[str]) -> tuple[list[str], set[str]]:
    """Validate every ``*.rules.yml``. Returns (problems, job names referenced)."""
    problems: list[str] = []
    jobs_referenced: set[str] = set()

    rule_files = sorted(ALERTS_DIR.glob("*.rules.yml"))
    if not rule_files:
        return (
            [
                f"{ALERTS_DIR.relative_to(REPO_ROOT)} contains no *.rules.yml. "
                "DG-6 is only closed while a rule file consumes the fail-open counter."
            ],
            jobs_referenced,
        )

    consumes_fail_open_counter = False

    for path in rule_files:
        rel = path.relative_to(REPO_ROOT)
        try:
            doc: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{rel}: not valid YAML — {exc}")
            continue

        groups = (doc or {}).get("groups")
        if not isinstance(groups, list) or not groups:
            problems.append(f"{rel}: expected a non-empty top-level `groups:` list.")
            continue

        # Recording rules may be referenced by alerts in the SAME file, so
        # collect them before validating expressions.
        recorded = {
            rule["record"]
            for group in groups
            if isinstance(group, dict)
            for rule in group.get("rules", []) or []
            if isinstance(rule, dict) and "record" in rule
        }

        for group in groups:
            if not isinstance(group, dict) or not group.get("name"):
                problems.append(f"{rel}: a group has no `name`.")
                continue
            gname = group["name"]
            rules = group.get("rules") or []
            if not rules:
                problems.append(f"{rel}: group `{gname}` has no rules.")
            for rule in rules:
                if not isinstance(rule, dict):
                    problems.append(f"{rel}: group `{gname}` has a non-mapping rule.")
                    continue
                name = rule.get("alert") or rule.get("record")
                if not name:
                    problems.append(
                        f"{rel}: group `{gname}` has a rule that is neither "
                        "`alert:` nor `record:`."
                    )
                    continue
                expr = rule.get("expr")
                if not isinstance(expr, str) or not expr.strip():
                    problems.append(f"{rel}: `{name}` has no `expr`.")
                    continue

                jobs_referenced.update(_JOB_MATCHER_RE.findall(expr))
                if "rate_limit_check_skipped_total" in expr:
                    consumes_fail_open_counter = True

                for metric in sorted(metrics_referenced(expr)):
                    if metric in recorded or _resolve(metric, known):
                        continue
                    problems.append(
                        f"{rel}: `{name}` references `{metric}`, which is neither a "
                        "recording rule in this file nor a metric defined under "
                        "services/*/app or shared/. Renamed or deleted in the "
                        "source? The alert would parse and never fire."
                    )

                if "alert" not in rule:
                    continue

                # An alert without `for:` pages on a single scrape spike; an
                # alert without severity cannot be routed; an alert without a
                # description wakes someone at 03:00 with a metric name.
                if "for" not in rule:
                    problems.append(f"{rel}: alert `{name}` has no `for:` duration.")
                labels = rule.get("labels") or {}
                if not labels.get("severity"):
                    problems.append(f"{rel}: alert `{name}` has no `severity` label.")
                annotations = rule.get("annotations") or {}
                for required in ("summary", "description", "runbook"):
                    if not annotations.get(required):
                        problems.append(
                            f"{rel}: alert `{name}` has no `{required}` annotation."
                        )

    if not consumes_fail_open_counter:
        problems.append(
            "No rule references `rate_limit_check_skipped_total`. That counter is "
            "the whole reason ops/alerts/ exists (code review DG-6); a rules "
            "directory that no longer consumes it has quietly reopened the finding."
        )

    return problems, jobs_referenced


def check_scrape_example(jobs_referenced: set[str]) -> list[str]:
    problems: list[str] = []
    rel = SCRAPE_EXAMPLE.relative_to(REPO_ROOT)
    if not SCRAPE_EXAMPLE.is_file():
        return [
            f"{rel} is missing. The rules cannot be installed without it: "
            "/metrics requires a bearer token (M-6), so a scrape config written "
            "from memory 401s and every rule evaluates over no data."
        ]

    try:
        doc: Any = yaml.safe_load(SCRAPE_EXAMPLE.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{rel}: not valid YAML — {exc}"]

    configs = (doc or {}).get("scrape_configs")
    if not isinstance(configs, list) or not configs:
        return [f"{rel}: expected a non-empty `scrape_configs:` list."]

    defined_jobs: set[str] = set()
    for config in configs:
        if not isinstance(config, dict):
            problems.append(f"{rel}: a scrape_config entry is not a mapping.")
            continue
        job = config.get("job_name")
        if not job:
            problems.append(f"{rel}: a scrape_config has no `job_name`.")
            continue
        defined_jobs.add(job)

        if config.get("metrics_path") != "/metrics":
            problems.append(
                f"{rel}: job `{job}` does not set `metrics_path: /metrics`."
            )

        auth = config.get("authorization") or {}
        has_file = bool(auth.get("credentials_file") or config.get("bearer_token_file"))
        if not has_file:
            problems.append(
                f"{rel}: job `{job}` carries no bearer credentials. /metrics is "
                "gated by METRICS_TOKEN (shared/metrics_auth.py) — without this "
                "every scrape is a 401 and the alerts are silently blind."
            )
        # An inline token here would be a committed secret, and rotating it would
        # mean a commit. credentials_file is re-read per scrape.
        if auth.get("credentials") or config.get("bearer_token"):
            problems.append(
                f"{rel}: job `{job}` inlines a bearer token. Use "
                "`authorization.credentials_file` — an inline value is a secret "
                "in git."
            )

    for job in sorted(jobs_referenced - defined_jobs):
        problems.append(
            f'A rule matches job="{job}" but {rel} defines no such job. The '
            "expression is valid, permanently empty, and indistinguishable from "
            "healthy."
        )

    return problems


def main() -> int:
    known = known_metric_names()
    if not known:
        sys.exit(
            "FAIL: found no metric definitions under services/*/app or shared/. "
            "The checker is looking in the wrong place — fix it rather than "
            "letting it pass vacuously."
        )

    problems, jobs_referenced = check_rule_files(known)
    problems.extend(check_scrape_example(jobs_referenced))

    if problems:
        print("Alerting configuration BROKEN:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("\nSee ops/alerts/README.md.", file=sys.stderr)
        return 1

    rule_files = sorted(p.name for p in ALERTS_DIR.glob("*.rules.yml"))
    print(
        f"ops/alerts OK: {len(rule_files)} rule file(s) "
        f"({', '.join(rule_files)}) parse, every referenced metric exists in the "
        f"service source, and the scrape config carries bearer credentials."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
