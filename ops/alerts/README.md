# Alerting — `ops/alerts/`

Prometheus rules and the scrape configuration they depend on.

**Why this directory exists.** Code review 2026-08-07 finding **DG-6**
(CWE-778, OWASP A09:2021): `data_gateway/app/rate_limit.py` exports
`rate_limit_check_skipped_total`, the counter that says "brute-force protection
is currently switched off", and **nothing in the repository consumed it** — no
rules, no dashboards, no scrape config. The counter half of the fix was done;
the alerting half was not. A metric with no consumer is instrumentation, not
observability.

| File | What it is |
|---|---|
| `rate_limit_fail_open.rules.yml` | Recording + alerting rules for the two `data_gateway` controls that fail open, plus the scrape-liveness guard |
| `prometheus-scrape.example.yml` | The scrape config the rules require — **not optional**, see below |
| `ops/ci/check_alert_rules.py` | Structural gate run by CI; parses both files and checks every metric they reference still exists in the service source |

---

## The scrape config is a prerequisite, not an afterthought

Since security finding **M-6**, `GET /metrics` is gated by
`Authorization: Bearer $METRICS_TOKEN` in all four services
(`shared/metrics_auth.py`). The failure mode this creates is quiet:

* wrong or missing token → **401** on every scrape
* `APP_ENV=production` with `METRICS_TOKEN` unset → **404** (fail closed; 404
  rather than 403 so an unauthenticated prober cannot tell the endpoint apart
  from a service that never registered the route)

In both cases Prometheus stores nothing, every rule here evaluates over an empty
series set, and **no alert fires** — which is indistinguishable from a healthy
platform. `MetricsScrapeDown` in the rules file exists precisely to make that
state loud; it is the one alert that must be wired before the others mean
anything.

---

## Installing

### 1. Generate and distribute the token

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Set it as `METRICS_TOKEN` for all four services. It is already declared as a
setting everywhere:

* `render.yaml` — `envVarGroups: intants-shared` (`sync: false`; one value
  shared by all four services)
* `docker-compose.prod.yml` — via the service `env_file`
* HF Space — Space secret
* `services/*/.env.example` and `space.env.example` ship it blank

Write the same value into a file Prometheus can read, e.g.
`/etc/prometheus/metrics_token` (mode `0400`). Do **not** inline it into
`prometheus.yml`: `credentials_file` is re-read on every scrape, so rotation
needs no reload and no commit.

### 2. Install the rules

```
/etc/prometheus/prometheus.yml          <- scrape_configs from prometheus-scrape.example.yml
/etc/prometheus/rules/*.rules.yml       <- rate_limit_fail_open.rules.yml
/etc/prometheus/metrics_token           <- 0400, the shared bearer token
```

Validate before reloading — `promtool` is the authoritative checker (CI's
`check_alert_rules.py` does not speak PromQL):

```bash
docker run --rm -v "$PWD/ops/alerts":/rules --entrypoint /bin/promtool \
  prom/prometheus:v3.1.0 check rules /rules/rate_limit_fail_open.rules.yml
```

### 3. Point the alerts somewhere a human sees

The rules emit `severity: warning` and `severity: critical`. Route `critical`
(`RateLimitFailingOpenSustained`, `MetricsScrapeDown`) to a paging channel;
`warning` to the ops channel. An alert that lands only in a dashboard recreates
the DG-6 defect one layer up.

---

## Reaching `/metrics` in each deployment

The three topologies this repo actually has differ in a way that matters here,
and only one of them lets a scraper in without extra work.

### `docker-compose.prod.yml` — Caddy in front, services on the `intants` network

Caddy answers `403` to `/metrics*` at the public edge (`Caddyfile:46`), and the
services publish nothing (`expose:`, not `ports:`). **Prometheus must join the
`intants` network** and scrape the container names directly — that is what the
targets in `prometheus-scrape.example.yml` assume:

```yaml
  prometheus:
    image: prom/prometheus:v3.1.0
    networks: [intants]
    volumes:
      - ./ops/alerts:/etc/prometheus/rules:ro
```

The bearer token is still required: `metrics_auth` is an application-layer
check, deliberately independent of the edge, because the edge is not present in
every topology.

### `render.yaml` — no proxy, one public hostname per service

Each backend is directly reachable on the internet. This is the deployment M-6
was written for: `METRICS_TOKEN` is the **only** control on `/metrics` here.
Change `scheme: http` to `https` and the targets to the Render hostnames.

### HF Space — everything behind one Caddy on loopback

`space/Caddyfile:60` returns `403` for `/metrics*`, and the services bind
`127.0.0.1`. There is no way in from outside, by design. Scraping the Space
means running the collector inside it; in practice the Space is a demo target
and is not scraped.

---

## What fires, and what it means

| Alert | Severity | Fires when | Blast radius while firing |
|---|---|---|---|
| `RateLimitFailingOpen` | warning | Any skipped rate-limit check, sustained 2 min | Login/register/password-reset unthrottled; `logout_all` not revoking |
| `RateLimitFailingOpenSustained` | critical | Same condition, 15 min | As above — treat as a security incident and review auth logs for the window afterwards |
| `MetricsScrapeDown` | critical | A target unscrapeable for 5 min | Every other alert here is blind |
| `ClientIpProxyHopUnderflow` | warning | `TRUSTED_PROXY_COUNT` higher than the real hop count, 10 min | Per-IP rate limiting collapses to one global bucket; consent IP hash records the proxy |

Two limits, recorded so nobody reads silence as safety:

1. **`absent()` is not used** on `rate_limit_check_skipped_total`. The counter
   is labelled `(bucket, error_type)`, and `prometheus_client` does not export a
   labelled child until it is first incremented — so the series is legitimately
   absent when everything is healthy, and `absent()` would fire forever.
   Pipeline staleness is covered by `MetricsScrapeDown` instead.
2. **`ClientIpProxyHopUnderflow` is one-directional** (code review DG-3). It
   catches `TRUSTED_PROXY_COUNT` set too *high*. Set too *low*, the extractor
   trusts a client-supplied hop, every attacker gets a private rate-limit
   bucket, and no counter moves. A quiet `ClientIpProxyHopUnderflow` does not
   mean the proxy count is right.

---

## Adding a rule

1. Add it to a `.rules.yml` file in this directory.
2. Every metric name the expression references must exist in
   `services/*/app/**.py` as a `Counter`/`Histogram`/`Gauge` — `check_alert_rules.py`
   enforces this, so renaming a counter in the source turns CI red here rather
   than silently retiring the alert.
3. Run `promtool check rules` (above) before merging.
