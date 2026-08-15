# Accepted Risk Register

> Created 2026-08-07 from the CONSIDER items in
> [`code-review-2026-08-07-domains.md`](code-review-2026-08-07-domains.md) whose
> correct resolution is **a recorded decision, not a code change**.

## Why this file exists

The grading table in every code review says a CONSIDER item is *"a judgment
call; doing nothing is defensible **if recorded**"*. Until now there was nowhere
to record one, so "defensible no-action" and "nobody looked at it" were
indistinguishable in the tree — and the same findings came back as new in the
next review. This is that place.

**These entries are OPEN risks. Nothing here is fixed.** An entry earns its
place by naming three things a reader can act on:

| Field | Why it is mandatory |
|---|---|
| **Owner** | A risk with no owner is a risk nobody re-reads. |
| **Trigger** | The concrete event that forces a re-decision. Not a date — dates slip silently; triggers fire. |
| **What is NOT true** | The compensating control a reader might assume exists. Half of these findings were originally raised because someone assumed one. |

Rules of use:

1. **Never mark an entry closed here.** Closing happens by fixing the code and
   deleting the entry in the same change, citing the commit.
2. **Never soften an entry to make a bid answer easier.** AR-1 in particular is
   a disqualifier, and writing around it is how a false compliance claim gets
   into a tender response.
3. When a trigger fires, the entry is re-decided in a review — extended with new
   evidence, or closed by a fix. It does not lapse.

---

## AR-1 — The demo tier is not India-resident

| | |
|---|---|
| **Source finding** | DPDP-3 (MEDIUM / CONSIDER) |
| **Status** | **ACCEPTED — blocking for any India-residency bid** |
| **Owner** | `platform_owner` (support@intants.com), with `cto-architect` for the migration |
| **Trigger to revisit** | Any of: (a) an RFP/L1 submission that asserts data residency; (b) a paying customer whose contract names DPDP §16 cross-border terms; (c) AWS Bedrock Mumbai approval landing, which unblocks the Tier-2 path |

**The decision.** The Tier-1 demo deployment runs on non-India infrastructure
and we are shipping it anyway, because the alternative is not shipping until
Tier-2 exists. This is a deliberate, time-boxed trade, not an oversight.

**Evidence, already documented.** This was *not* discovered by the review —
[`DATA-FLOW.md`](DATA-FLOW.md) opens with an explicit residency banner
(`:12-24`), carries a per-processor region table (`:32-50`) and a written
Tier-2 mapping (`:99-113`). The review's contribution is grading the residual
risk, not finding it. Summary of where data actually sits: **Neon** Singapore,
**Cloudflare R2 / Gemini / Groq / Tavus / Simli / LiveKit / Resend / OpenAI /
Sentry** United States, **Upstash** global edge, the backend VM in an
operator-chosen non-India region. **Sarvam** (speech) is the one processor
confirmed in India.

**Blast radius.** Not zero. This entry used to say "zero for the demo tier,
which is what the banner is for" — but a banner is a disclosure, and disclosure
changes who is *surprised* by an exposure, not whether the exposure exists. The
honest position is three separate numbers, because conflating them is what
produced the "zero":

* **Candidate data — the full disclosed cross-border exposure.** Every account
  record, voice recording, transcript and scorecard created on the demo tier is
  processed outside India, by every row of [`DATA-FLOW.md`](DATA-FLOW.md)'s
  sub-processor table except **Sarvam** (India) and **JDoodle** (country
  unverified — see AR-3). That is true for every candidate who takes an
  interview on this tier today, consent modal or not.
* **Bid marks — zero, as of this entry's last review.** The Status line's
  blocking rule has held: nothing has been submitted on the demo tier, so no
  evaluation score has been lost to this. This is the only sense in which "zero"
  was ever defensible, and it is a statement about what we have not done, not
  about the platform. If a submission is ever made on this tier, the trigger
  above has already fired and this bullet is void.
* **Bid eligibility — total.** APSSDC/NSDC residency clauses are pass/fail, so
  the demo tier blocks submission outright rather than costing marks. This is
  why the Status line reads *blocking for any India-residency bid*.

**What is NOT true.** There is no partial-residency story worth claiming. Do not
describe the platform as "India-resident with some processors abroad" — the
primary database, all object storage and every LLM call are outside India.

**Path to closure.** Tier-2 (AWS Mumbai) per [`Final_stack.md`](Final_stack.md)
and the checklist in [`PROCUREMENT.md`](PROCUREMENT.md). Same code,
environment-swappable. Blocked on Bedrock approval, Bhashini ULCA approval, and
a commercial contract.

---

## AR-2 — One symmetric HS256 secret signs and verifies for every service

| | |
|---|---|
| **Source finding** | SEC-2 (MEDIUM / CONSIDER), with SEC-1 (no `kid`, no rotation) as its sibling |
| **Status** | **ACCEPTED for the demo tier — asymmetric signing is the Tier-2 answer** |
| **Owner** | `security-auditor`, with `cto-architect` on the key-management design |
| **Trigger to revisit** | Any of: (a) a fifth service or any third-party integration needing to *verify* our tokens — verification would hand them signing power; (b) the first real `JWT_SECRET` exposure or suspected exposure; (c) Tier-2 migration; (d) any customer contract with a key-management or key-rotation clause |

**The decision.** All four services plus the LiveKit worker share one HS256
`JWT_SECRET` (`shared/auth/jwt.py`). Under HS256 the verification key *is* the
signing key, so read access to any one environment mints tokens for any `sub`
and any `roles` — **including the `service` role**. The blast radius of
compromising analytics-only `admin_ops` equals that of compromising
`data_gateway`, which holds auth. We accept this for the demo tier because the
fix is a signing-algorithm migration (RS256/EdDSA + a JWKS or key file + `kid`
in the header + a rotation runbook), which is Tier-2-sized work.

**Interim mitigation — rotation is now at least possible.**
`verify_access_token(token, secret, ...)` accepts **either a single key or a
sequence of keys**, tried in order (`shared/auth/jwt.py:147-165`). That makes an
overlap window expressible: publish the new key to every verifier first, then
cut signing over, then retire the old one. It does **not** reduce the blast
radius — every verifier still holds every signing key. It converts an outage
into a procedure.

**What is NOT true.**
* There is **no `kid` header**, so a token does not say which key signed it —
  multi-key verification is trial-and-error across the list, not selection.
* There is **no automated rotation** and no rotation runbook yet. The capability
  exists; the procedure does not.
* Token-epoch revocation **fails open** in all verifiers (SEC-3) — a Redis
  outage means revoked tokens verify until they expire. That is a separate
  deliberate availability trade, and it means "we can revoke" is only true while
  Redis is up.

**Path to closure.** Asymmetric signing at Tier-2: `data_gateway` holds the
private key and is the only signer; the other services and the worker verify
with the public key only. That makes compromising `admin_ops` unable to mint
anything, which is the actual goal.

---

## AR-3 — Candidate-authored code executes on JDoodle, a third party

| | |
|---|---|
| **Source finding** | AG-05 (MEDIUM / CONSIDER) |
| **Status** | **ACCEPTED for the demo tier — self-hosted Piston is the production answer** |
| **Owner** | `platform_owner` for the vendor question; `devops-engineer` for the Piston self-host |
| **Trigger to revisit** | Any of: (a) an India-residency bid (AR-1's trigger fires this one too); (b) a customer whose coding questions are confidential IP; (c) exceeding JDoodle's free tier, which forces a commercial decision anyway; (d) a DPA request from any customer covering the coding round |

**The decision.** `execution_provider` defaults to `jdoodle`
(`services/data_gateway/app/config.py:194`), and every deploy target sets it
explicitly: `space/entrypoint.sh:121` and `docker-compose.prod.yml:106`. So
candidate-authored source code — plus any custom stdin the candidate typed —
leaves our infrastructure to a hosted third party on every coding round, while
the hardened self-hosted Piston path exists and is unused by default.

We accept this because self-hosting Piston needs a VM with a privileged
container, and the whole point of the current deploy shape (one HF Space; one
small Oracle VM) is that it does not need one. See
[`PISTON_SELFHOST.md`](PISTON_SELFHOST.md).

**Residency claim CORRECTED, not accepted.** `DATA-FLOW.md:46` previously said
JDoodle processes in **"India (JDoodle infrastructure)"**. That is a residency
statement in the document the consent modal links to, and we have no evidence
for it: the API base is `https://api.jdoodle.com/v1` with no region selector, no
region is negotiated in the client, and there is no DPA or contractual term on
file. The row now reads **unverified** rather than asserting a country. Do not
restore the India claim without a written statement from the vendor.

**What is NOT true.**
* JDoodle is **not** covered by a Data Processing Agreement. It appears in the
  sub-processor table as a disclosure, which is the minimum, not the control.
* The `piston` alternative's *default* URL is the **public** `emkc.org` endpoint
  (`config.py:200`), which has been whitelist-only since 2026-02-15 and is
  itself third-party. Switching `EXECUTION_PROVIDER=piston` without also setting
  `PISTON_API_URL` to your own instance moves the risk, it does not remove it.
* No interview audio, transcript or profile data reaches JDoodle — code and
  stdin only. That bounds the exposure; it does not make it zero, because a
  candidate's submitted code is their own work product.

**Path to closure.** Self-host Piston in the deploy region (Mumbai at Tier-2)
and flip the default to `piston` with `PISTON_API_URL` pointed at it. This is a
config change, not a code change — the execution client is already swappable.

---

## AR-4 — The demo avatar has no production gate, and the Tier-2 avatar is not built

| | |
|---|---|
| **Source finding** | AG-06 (documentation fix landed; these two gaps are what the corrected document exposed) |
| **Status** | **ACCEPTED — operator discipline, not an enforced control** |
| **Owner** | `cto-architect` for the `custom` renderer; `cfo-cost-watcher` for the spend side |
| **Trigger to revisit** | Any of: (a) `APP_ENV=production` being set on a deploy that faces real candidates at volume; (b) an India-residency bid (AR-1); (c) the 2026-11-28 sunset review in `Final_stack.md` TIER 1B |

**The decision.** [`Final_stack.md`](Final_stack.md) TIER 1B used to mandate that
the avatar adapter *"hard-refuses when `APP_ENV=production`"*. That gate was
written for D-ID, which was removed on 2026-05-31, and it was never rebuilt for
Tavus or Simli. `_build_avatar()` in
`services/interview_core/app/worker/interview_worker.py` selects purely on
`AVATAR_PROVIDER` and never reads `APP_ENV`.

Separately, `AVATAR_PROVIDER=custom` — the Tier-2 / bid-path value named in
`CLAUDE.md` and `config.py:169` — is **not a recognised value in the avatar
factory**. `_build_avatar()`'s first branch matches any value outside
`{"tavus", "none"}` (`interview_worker.py:1284-1297`), so `custom` logs an
unknown-provider warning and returns a `simli.AvatarSession`. A bid deploy that
sets `custom` believing it has opted out of US-hosted avatars would get a
US-hosted avatar.

**Documentation consequence, corrected 2026-08-09.** `Final_stack.md` TIER 1B
standing condition 1 used to *require* `AVATAR_PROVIDER=custom` for APSSDC /
government deploys. That turned this gap from a missing feature into an active
trap: following the procurement document produced the exact outcome it forbade.
The condition now states the real position — **government deployment is blocked
until the Tier-2 renderer exists, on any `AVATAR_PROVIDER` value** — and names
`none` (voice-only) as the only setting that adds no US-hosted avatar processor,
while noting that `none` does not make a deploy India-resident either (AR-1).
The two documents must move together: if the renderer lands, both this entry and
that condition close in the same change.

**What is NOT true.** Neither "the production gate" nor "the Tier-2 avatar" is
implemented, and there is **no avatar configuration that makes a government
deploy compliant**. `Final_stack.md` TIER 1B now says so explicitly rather than
implying otherwise in a new vendor's name — which is the exact mistake AG-06
was raised about.

**Path to closure.** Two separate pieces of work, in this order:
1. Make the unknown-provider fallback **fail loudly** instead of silently
   selecting Simli, so `custom` cannot be mistaken for implemented.
2. Build the Three.js + Ready Player Me + Rhubarb client-side renderer, then add
   the `APP_ENV=production` refusal for `tavus`/`simli` once there is a
   compliant provider to refuse *into*. Adding the refusal first would only
   break the demo.

---

## Index

| ID | Risk | Source | Owner | Fires when |
|---|---|---|---|---|
| **AR-1** | Demo tier is not India-resident | DPDP-3 | `platform_owner` | Residency-asserting bid, or Bedrock Mumbai approval |
| **AR-2** | One shared HS256 secret across five processes | SEC-2 / SEC-1 | `security-auditor` | Fifth verifier, secret exposure, or Tier-2 |
| **AR-3** | Candidate code executes on JDoodle | AG-05 | `platform_owner` | Residency bid, confidential-IP customer, or free-tier exhaustion |
| **AR-4** | No production avatar gate; `custom` unimplemented | AG-06 residue | `cto-architect` | Production `APP_ENV`, residency bid, or 2026-11-28 sunset review |
