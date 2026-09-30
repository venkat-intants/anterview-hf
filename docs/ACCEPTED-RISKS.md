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
| **Status** | **CAPABILITY SHIPPED 2026-09-28, NOT YET LIVE ANYWHERE — asymmetric signing exists in code; every current deployment still signs HS256, and one HS256 signer outside `data_gateway` remains by design (see addendum)** |
| **Owner** | `security-auditor`, with `cto-architect` on the key-management design |
| **Trigger to revisit** | Any of: (a) a fifth service or any third-party integration needing to *verify* our tokens — verification would hand them signing power; (b) the first real `JWT_SECRET` exposure or suspected exposure; (c) Tier-2 migration; (d) any customer contract with a key-management or key-rotation clause; (e) the RS256 rollout below actually being run against a deployment — this entry stays open until it is |

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

**2026-09-28 addendum — the capability now exists; nothing has adopted it yet.**
`shared/auth/jwt.py` gained RS256 issue/verify (EdDSA was the first choice but
`python-jose==3.5.0` as pinned has no `EdDSA` member in `jose.constants.
ALGORITHMS` — verified against the installed package, not assumed — so RS256
is what this dependency actually supports), a `kid` header, and
`VerificationKey` so a verifier can hold several public keys and a caller can
hold both an HS256 secret and RS256 keys at once. `data_gateway`'s `Settings` gained
`jwt_signing_algorithm` / `jwt_private_key` / `jwt_active_kid`; the other three
services' `Settings` classes gained `jwt_verify_algorithms` / `jwt_public_keys`
and NOTHING ELSE — no `jwt_private_key` field exists on them at all, and
`shared.security.forbid_private_signing_key` refuses to boot any of them if one
reaches their environment anyway (no environment exemption — it is wrong in
dev too). `scripts/generate_jwt_rsa_keypair.py` generates a keypair and prints
every setting an operator needs, in rollout order.

**What this addendum does NOT claim.**
* **Nothing is deployed this way.** Every default is HS256-only
  (`JWT_SIGNING_ALGORITHM=HS256`, `JWT_VERIFY_ALGORITHMS=HS256`), so a
  deployment that sets none of the new variables is byte-for-byte today's
  behaviour. Turning RS256 on is a 4-step operator rollout (public keys +
  dual-verify everywhere, THEN cut the issuer over, THEN wait out the token
  TTL, THEN drop HS256 — see `shared/auth/jwt.py`'s module docstring) that has
  not been run against any real environment.
* **The HF Space demo deployment cannot safely turn this on as it stands.**
  `space/entrypoint.sh` `export`s one shared environment into all four
  supervisord-managed processes in a single container — there is no
  per-service `.env` split the way the Railway/VM deploy has. Setting
  `JWT_PRIVATE_KEY` in `space.env` hands it to all four processes, and
  `forbid_private_signing_key` will then refuse to boot the three that must
  never hold it. That guard doing its job, not a bug, but it means the Space
  needs an `entrypoint.sh` change (scope `JWT_PRIVATE_KEY` to the
  `data_gateway` process only) before RS256 can be turned on there — devops
  work, not done as part of this change.
* **One HS256 signer outside `data_gateway` remains, unchanged, by design.**
  `interview_core`'s worker (`_mint_service_jwt` in
  `app/worker/interview_worker.py`) still self-signs a `sub="interview_core",
  roles=["service"]` token with the shared `JWT_SECRET` to call
  `feedback_billing`'s `/internal/score` directly — this is precisely the
  AR-2 exposure, for that one call path, and it is not this change's doing to
  leave it but it is this change's job to say so plainly: closing it needs
  `interview_core` to obtain a signed token from `data_gateway` instead of
  minting one itself (a new internal RPC — endpoint, auth for it, client,
  tests), which is materially larger than a JWT-library change and was judged
  out of scope here. The functional fallback already exists independently of
  this decision: `data_gateway`'s own reconciler retries a `/internal/score`
  call that failed for any reason, so once HS256 is fully retired platform-
  wide this one path degrades to "scored via the reconciler, not immediately"
  rather than breaking outright — but the security exposure this paragraph
  describes is real until either HS256 retires or this path is redesigned.

---

**PH4 Wave 4 addendum (2026-09-20).** The offer-link secret and the HRMS export
signing key, when `OFFER_LINK_SECRET` / `HRMS_EXPORT_SECRET` are not set, are
DERIVED from `JWT_SECRET` (namespaced HMAC, never the secret itself). Rotating the
key an HRMS verifies against then means rotating the JWT secret. Set both
explicitly in any deployment that hands exports to a real HRMS.

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

## AR-5 — Free-text reason/rationale fields are redacted on erasure (CLOSED)

| | |
|---|---|
| **Source finding** | PH4 Wave 1 security audit, M4(b), 2026-09-17 |
| **Status** | **CLOSED 2026-09-26** — redacted at the source, in the erasure executor, not merely documented. Closed per this register's own rule 1: fixed in code, entry kept (not deleted) because roughly a dozen other files cite it by name as a stable anchor — see "Why this entry is kept, not deleted" below. |
| **Owner** | `platform_owner` (support@intants.com) — accountable for the fix landing; `security-auditor` to confirm on next review. |
| **Closed by** | `services/admin_ops/app/erasure_executor.py` step 5l + migration `f2a4c6e8b0d3` (`services/data_gateway/alembic/versions/20260926_0001_f2a4c6e8b0d3_ar5_erasure_redaction.py`). |

**What this was.** When HR recorded a hire or a reject, the free-text reason
they typed was written in two places — the stage ledger
(`stage_transitions.reason`) and the append-only audit log (`details.reason` on
`enrolment.decision.*`, or `details.rationale` on `applicant.decision.*`) — and
neither table's append-only trigger had any redaction exception, so DPDP
erasure anonymised the applicant (step 6) while the prose describing them lived
on forever in both stores. A rationale such as "Priya's notice period at Acme is
six months" survived, attached to an anonymised applicant. **A fresh search for
the same class of field, not just the two named above, also found:**
`enrolments.held_reason` (why HR held this candidate),
`enrolments.reapply_override_reason` (why HR let them back in after a
rejection) and its own audit row (`enrolment.reapply_override`), and
`round_results.evidence` on a `human_review` round — a reviewer's own verdict
note
(`hr_workflows.py::post_round_review`'s `note`), not the AI prose the rest of
that table's exclusion argument rests on. All were the same shape: a human's
own words about a specific candidate, kept forever by an append-only structure
built before anyone asked what erasure does to it.

**What changed.** Two triggers each gained exactly one new permitted UPDATE
shape — `redacted_at` NULL -> now(), the free-text column (or JSONB key) moving
to the fixed marker `'[redacted]'`, and nothing else on the row changing — on
the `interviewer_scorecards_protect()` pattern this entry's own path-to-closure
named. A matching CHECK constraint on each table holds the same invariant
independently of the trigger, so a row cannot carry live prose next to a
non-NULL `redacted_at` even if the trigger were ever disabled by mistake.
`enrolments.held_reason`/`reapply_override_reason` and `round_results.evidence`
needed no schema change — neither table was append-only — so erasure step 5l
redacts all four in one place: `stage_transitions.reason`,
`audit_log.details.{reason,rationale}` on the three decision/override actions,
`enrolments.held_reason`/`reapply_override_reason`, and `round_results.evidence`
on a human-graded row. Scores, categories, ids, timestamps and who acted are
untouched everywhere — only the prose a person wrote is gone.

**What is still true, and is not this risk.** `applicants.ats_*` /
`enrolments.ats_*` prose is AI-generated commentary on a resume, not a human's
own words about the candidate — outside this entry's scope, both before and
after this fix, and unchanged by it. `corpus_chunks.content` (AR-8) is a
different, structurally distinct gap: text an HR manager pasted into a company
document that this executor has no key to join on, so a match can only ever be
found (since 2026-09-28) by a subject-specific name search and reported to a
human — never redacted automatically. Nothing in this fix touches either.

**PH5-E3 (talent pools) is unaffected, and was never part of this gap.**
`talent_pool_events` is append-only, on the `task_events` / `document_events`
precedent, and keeps a `member_added` (and `member_removed`,
`member_evidence_reviewed`, `member_invited`) row naming the `applicant_id` a
pool action concerned. It was DESIGNED facts-only from the start (action, ids,
counts, a freshness band, a note's LENGTH — never the note, a removal reason's
text, a name or any CV/scorecard prose), so after step 6 anonymises the
applicant it names, the row is structurally bounded to "an anonymised id was
once part of an action" — never free text that could describe a person. This
was recorded against AR-5 as a scope note rather than a new entry; it is
restated here, against the closed entry, for the same reason: not a fresh gap,
and not something this fix needed to touch. See
`erasure_executor.py::EXCLUDED_TABLES["talent_pool_events"]`.

**Why this entry is kept, not deleted, despite rule 1 above.** `DATA-FLOW.md`,
both PH4/PH5 acceptance checklists, the evidence graph
(`app/evidence_graph/`), `rediscovery.py`, `hire_checkins.py` and
`routers/agent.py` all cite "AR-5" by name as an established fact about the
platform. Deleting the entry would turn every one of those into a dangling
reference to nothing; keeping it, closed, lets each citation keep resolving to
an accurate answer. The evidence graph in particular has its own reason to
keep working exactly as it did: `candidate.erased` there goes true the moment
an erasure is *requested*, not when the 30-day-later executor run actually
redacts anything, so `_decision_nodes`' own withholding of
`stage_transitions.reason` is still the only thing standing between a reader
and live prose during that window. This fix closes the gap that mattered
forever (the database after erasure runs); it does not, and was never asked
to, shorten the 30-day grace period the whole erasure design already commits
to elsewhere.

---

## AR-6 — Preboarding documents, task artifacts and materials are not scanned for malware

| | |
|---|---|
| **Source finding** | PH4 decision D4-3 (Wave 4, A4), 2026-09-20; extended to job-simulation/portfolio uploads (PH4 Wave 5, D4), 2026-09-22 |
| **Status** | **ACCEPTED — allow-listed and isolated, not scanned** |
| **Owner** | `platform_owner` (support@intants.com) — accountable; `security-auditor` reviews when a trigger fires. |
| **Trigger to revisit** | Any of: (a) a customer or bid that requires malware scanning of uploads; (b) any feature that renders an uploaded document in the browser or processes it server-side (thumbnails, OCR, conversion); (c) a single report of a malicious document; (d) the Tier-2 migration, where AWS offers a managed scanner |

**The decision.** After an offer is accepted, a candidate uploads identity and
other documents that HR managers then open. The same is now true of a
job-simulation or portfolio round: a candidate's own file response
(`app/job_tasks.py::add_artifact`) and an HR-attached reference material
(`add_material`) both go through the same document check. There is no
antivirus scan (no ClamAV or managed equivalent) anywhere in this chain —
decision D4-3 chose a strict allow-list now over a scanner later, and PH4-D4
reuses that decision rather than writing a second one. What exists instead:

- **Content allow-list.** Only PDF, JPEG and PNG, recognised by their first bytes
  (`app/document_storage.py`, reused unchanged by `job_tasks.py`); the file name
  and the browser's Content-Type are ignored. Anything else is refused before
  it is stored.
- **No active PDFs, as far as a byte scan sees.** A PDF whose names spell
  JavaScript, launch actions, embedded files, rich media or XFA — including
  when escaped as ``#xx`` — is refused.
- **Isolation in delivery.** Documents, artifacts and materials are never
  served by the API or rendered by the app. They leave storage only by a
  pre-signed link that lives five minutes and forces
  `Content-Disposition: attachment`, stored under a key that names no person.
- **Size.** 10 MB, enforced at the edge and again in the handler (preboarding
  documents, task artifacts and task materials each have their own limit
  setting, all defaulting to the same 10 MB).

**What is NOT true.** It is not true that an uploaded document, artifact or
material is known to be safe. The PDF check reads raw bytes, so a marker
inside a compressed object stream is not seen (escaped names ARE decoded); an
image can still exploit a vulnerable viewer. The control
is "only three well-understood formats, never opened by us", not "scanned".

**Path to closure.** Scan on upload before a document, artifact or material is
marked `submitted` (ClamAV in a sidecar, or the object store's managed
scanner at Tier 2), with a `quarantined` state the review trigger refuses to
verify.

**PH5-E2 amendment (2026-09-23) — the document corpus fires trigger (b).**
`app/corpus.py` parses a fourth upload path server-side (PDF, DOCX, TXT, MD
into the company's document library), which is exactly "any feature that ...
processes it server-side". Recorded here rather than treated as a new
decision, because the trigger firing does not by itself change the answer —
what changed is worth stating plainly:

- **The threat model is different, not absent.** Every uploader on this path
  is an authenticated `hr_manager` or `super_admin` of the tenant, not an
  anonymous candidate — the same person who could otherwise type the same
  content straight into the chat. This narrows, but does not remove, the
  surface: a compromised or malicious staff account is still a real actor, and
  a scanner would still be worth having against one.
- **The controls E2 adds, on top of D4-3's allow-list and isolation:**
  parsing runs in a thread (`app/corpus.py::extract_text`) with a
  **30-second bound on the REQUEST**, not on the thread itself —
  `asyncio.to_thread` can cancel the `await`, never the running thread, so
  what the timeout actually buys is that the caller gets its 422/500 back and
  the connection is freed at 30s; the worker THREAD is released only when the
  parser call returns, which is bounded in practice by the input the parser
  ever sees (the 10 MB upload cap, `pypdf`'s per-page loop, and DOCX's own
  caps below) rather than by the timeout cancelling anything. Security
  sign-off on this amendment is conditional on this paragraph, not the
  earlier "cannot hang a worker" wording it replaces, which overstated what
  `asyncio.to_thread` provides. DOCX is read with `defusedxml` (no DTD/entity
  expansion, so no XXE) and hard caps of **200 zip entries and 8 MB
  uncompressed**, enforced against ACTUAL bytes read, not only the archive's
  declared sizes; the PDF active-content refusal (`document_storage.py`) is
  unchanged and applies identically; and a corpus document is never rendered
  in-browser — download only, through the same five-minute signed link as
  every other document here. Uploads are also rate-limited per company
  (`CORPUS_UPLOAD_PER_MINUTE`, default 20/minute) as a coarse bound on how
  often the parse pool can be triggered at all.
- **No scanner added this wave**, per the recommendation on file. If a lead
  wants one, it is a separate story, not folded into this wave's checklist.

---

## AR-7 — Portfolio external links are never fetched, and reviewers see them cold

| | |
|---|---|
| **Source finding** | PH4 Wave 5 (D4), 2026-09-22 |
| **Status** | **ACCEPTED — validated and stored, never fetched server-side** |
| **Owner** | `platform_owner` (support@intants.com) — accountable; `security-auditor` reviews when a trigger fires. |
| **Trigger to revisit** | Any of: (a) a feature that previews, unfurls or screenshots a submitted link server-side; (b) a phishing or malware report traced to a portfolio link; (c) a customer requiring an allow-list stricter than a per-round domain list; (d) a request to fetch link metadata (title, favicon) for display |

**The decision.** A portfolio round accepts a link instead of, or alongside, a
file (a GitHub repo, a Figma file, a YouTube demo). Accepting an arbitrary
URL from a candidate and having the SERVER fetch it is a classic SSRF surface
(internal metadata endpoints, other tenants' presigned URLs, port-scanning the
platform's own network) — PH4-D4 closes that surface by construction rather
than by a scanner:

- **The server never fetches a submitted link.** `app/job_tasks.py::validate_link`
  checks scheme, host shape and the allow-list; it never opens a connection to
  the URL. There is no preview, no thumbnail, no title fetched server-side.
- **https only, no userinfo, no IP literal, no non-standard port.** Closes the
  most common SSRF and credential-leak shapes (`http://169.254.169.254/`,
  `https://user:pass@host/`, a bare IP, a link to an internal port).
- **A per-round allow-list, defaulting to nine known developer/portfolio
  domains**, matched on a dot boundary (`evilgithub.com` is refused for an
  allow-list of `github.com`) so a look-alike subdomain trick does not pass.
- **A reviewer sees an interstitial before following one** (frontend), so
  clicking through is the reviewer's own act, in their own browser, against
  their own network — not the platform's.

**What is NOT true.** It is not true that an allow-listed link is safe to
open. `github.com` and the other eight domains can host anything a candidate
puts there (a malicious repository, a redirect, a fake login page mimicking
the platform). The control is "the platform never becomes the requester",
not "the destination is vetted".

**Path to closure.** If link preview is ever wanted, fetch it from an
isolated, egress-restricted worker with no access to internal networks or
credentials, never from the request-serving process — and keep the interstitial
regardless.

---

## AR-8 — DPDP erasure can now flag, but still cannot remove, a candidate's name inside an HR-uploaded document

| | |
|---|---|
| **Source finding** | PH5 Wave 3 (E2 — document corpus RAG), 2026-09-23 |
| **Status** | **ACCEPTED — NARROWED 2026-09-28 (detection added), still not solved.** Erasure now searches for and reports a name match; it still cannot remove one. |
| **Owner** | `platform_owner` (support@intants.com) — accountable; `security-auditor` reviews when a trigger fires. |
| **Trigger to revisit** | Any of: (a) a customer or bid requiring erasure to REMOVE text inside uploaded documents, not merely flag it; (b) a corpus document confirmed (by the new detection, or otherwise) to contain candidate data; (c) any feature that auto-ingests candidate-derived content into the corpus |

**The decision, unchanged since Wave 3.** PH5-E2 gives a company's HR
managers and super admins a document library the staff copilot can search —
policies, handbooks, process notes. `corpus_chunks.content` is free text
extracted from whatever they upload, and there is no key from an applicant row
to a chunk of that text: not a foreign key, not a shared identifier, nothing
an erasure executor could join on to find it, let alone remove it. **If an
uploader pasted a real candidate's name into a document — a worked example in
a training handbook, an old memo copied in whole — this platform still cannot
ERASE it.** That is a real limit, not a gap to be quietly designed around, and
`services/admin_ops/app/erasure_executor.py::EXCLUDED_TABLES` says so for all
four corpus tables rather than presenting the inventory as complete.

**What changed 2026-09-28 — detection, not removal.** Before step 6 redacts
`applicants.full_name`, the erasure executor now runs a best-effort search
(`erasure_executor.py::_scan_corpus_and_notify`, step 5m) for the erasure
subject's own on-file name, scoped to the companies that subject actually has
an `applicants` row with — never a scan of every tenant's library for one
person's name. The search is the same full-text mechanism
`app/corpus.py::search_corpus` already uses (`to_tsvector`/`plainto_tsquery`
over `corpus_chunks.content`), so it needs no new index and no new capability
over what retrieval already does — it is a differently-scoped READ of the same
column. A hit:
- is recorded on the erasure's `artifacts` as **facts only** — document id,
  version, chunk count, company id — and the matched text is never read into
  that record, never logged, never quoted anywhere;
- raises one notification (`notifications.kind = "corpus_review_needed"`) per
  document to every staff member of that company who could read it
  (`hr_manager` always; `super_admin` too, unless the document is `hr_only`,
  which a `super_admin` could not open anyway), naming the document by its
  own title and linking to it, never quoting the matched passage;
- changes nothing else. Nothing is deleted, redacted, or even opened by the
  system on the strength of a match — a human decides.
The scan runs inside its own database SAVEPOINT and swallows any failure
(logged, not raised): a scan that cannot complete costs the erasure a finding,
never its completion, and a corpus hit is information, not grounds to fail or
delay the erasure itself.

**What exists instead — the pre-existing controls, none of them detection
until now:**

- **An upload-time attestation.** The upload dialog requires HR to confirm
  "This is a company document, not a record about a candidate" before the
  request is accepted (`app/corpus.py::ingest_document` refuses with
  `attestation_required` otherwise); the confirmation is recorded on the
  `corpus.document.uploaded` audit row.
- **`hr_only` as the UI's default audience for an `hr_manager` upload.**
  Qualified deliberately: this default applies only when the uploader IS an
  `hr_manager`. A `super_admin`'s upload is not "defaulted to `hr_only`" — it
  **can only ever be `all_staff`**, full stop (design decision Q3;
  `app/corpus.py::ingest_document` refuses `audience="hr_only"` from a
  `super_admin` with a 422). So the narrowest-population default is real for
  one uploader role and inapplicable, not merely different, for the other.
- **Immediate, complete purge on delete — the ORIGINAL FILE included, not
  only the derived chunks.** `app/corpus.py::delete_document` removes the
  chunks, the embeddings, AND the originally uploaded file from object
  storage (Cloudflare R2 in the demo tier) in the same request — no 30-day
  grace window — so a document uploaded in error can be fully gone within
  seconds of HR noticing, rather than waiting out a retention clock. Stated
  explicitly because the limit above is about TEXT an erasure executor cannot
  reach unprompted; the original file is a second copy of exactly the same
  risk, and the purge control covers both, not only the searchable copy.
- **A `super_admin` can never create or read back an `hr_only` document**
  (design decision Q3) — narrowing who could have put candidate-shaped text
  in front of the widest company-level audience in the first place.

**What is still NOT true, even after the narrowing.** It is not true that a
scan runs at upload — detection is triggered only by an erasure request, for
that one subject's own name, never a proactive check of what a document
contains when it is uploaded. It is not true that the scan reaches every
candidate — only someone who is later the SUBJECT of a DPDP erasure request is
ever searched for; nobody scans the corpus for candidates who never request
erasure. It is not true that a scanned PDF with no extracted text is covered
— the search runs over `corpus_chunks.content`, which is empty for exactly the
documents `no_text` already refuses at upload; there was never anything to
search there. It is not true that a name spelled, transliterated, abbreviated
or given as a nickname differently from the one on the subject's own
`applicants` row will be found — this is a literal, stemmed full-text match,
not semantic or fuzzy. It is not true that the ORIGINAL FILE in object storage
is reached — the search only ever touches the already-extracted, already-
chunked text sitting in Postgres, never the stored object. It is not true that
a company the subject has no `applicants` row with is searched — if a guest
application was never linked to the subject's account, that company's corpus
is not scoped in, even if it does hold text about them. And the pre-existing
structural claim still stands unchanged: **a retrieved document cannot change
system behaviour** (no write tool exists for a document to steer); it is still
not entitled to claim that **a retrieved document cannot influence the
model's prose** — see `shared/agents/guardrails.py` and
`app/corpus.py::detect_injection` usage, which reports an injection attempt
rather than claiming to neutralise it.

**Path to closure — unchanged, and detection does not shorten it.** Table-
stakes if this ever needs closing: a client-side PII scanner over extracted
text AT UPLOAD (report, do not block, on the steering-resume precedent, and
broader than today's erasure-triggered, subject-specific search), and/or a
documented process for HR to attest per-document that it contains no
third-party personal data, reviewed periodically, and/or a way to actually
REMOVE a confirmed match from a document's text rather than only ever
flagging it for a human to edit or delete the whole document by hand. None of
these is built.

---

## AR-9 — Gaze detection flags candidates for looking away, and looking away is not evidence

| | |
|---|---|
| **Source finding** | Camera proctoring for exams (code review + security review), 2026-09-29 |
| **Status** | **ACCEPTED — shipped, weighted lowest, never decisive, and separated in the HR UI** |
| **Owner** | `platform_owner` (support@intants.com) — accountable; `product-manager` re-decides when a trigger fires. |
| **Trigger to revisit** | Any of: (a) a candidate complaint, accessibility request or disability disclosure naming gaze or eye tracking; (b) a customer or bid asking us to raise its weight, rank candidates by it, or make it decisive; (c) evidence of a systematic false-positive pattern (a device class, a seating setup, a demographic); (d) any regulatory guidance on biometric inference under DPDP |

**The decision.** Camera proctoring emits three signals. Two of them —
`face_absent` and `multiple_faces` — describe facts about the room that a human
would read the same way. The third, `gaze_away`, infers *where a candidate is
looking* from face landmarks, and that inference does not carry the same weight:
people look away because they are thinking, because of a motor or visual
difference, because they use assistive technology, because a screen reader is
speaking, or because the room has a window. We shipped it anyway, because a
reviewer watching a recorded timeline genuinely wants to know the candidate
spent four minutes looking off-screen — but every structural choice around it
assumes it will sometimes be wrong about an innocent person:

- **Weighted 5, the lowest of the seven event types** (`exam_integrity_weight_gaze_away`),
  tied with `copy` and a fifth of `multiple_faces`.
- **Never a violation.** It is absent from `VIOLATION_EVENT_TYPES`, so it can
  never contribute to the auto-submit threshold — a candidate cannot have an
  exam ended by looking away, at any frequency.
- **Never decisive — and this claim is about `gaze_away` specifically, not
  about proctoring as a whole.** Because gaze is absent from
  `VIOLATION_EVENT_TYPES` it cannot reach the auto-submit threshold, and that
  mechanism is the reason it cannot reach any outcome. Be precise about the
  rest: the four signals that ARE violations can end an exam early, and
  `_grade_and_finalize` then writes `exam_attempts.passed` and `.status` and
  may advance the applicant's stage. Proctoring as a whole is therefore not
  consequence-free; gaze is. An earlier version of this entry claimed
  "nothing in the proctoring path writes a stage, a status or a decision",
  which was false and was corrected by the security review. `PanelVerdict`
  still has no field that can express a hiring outcome (CLAUDE.md hard
  constraint 9) — a separate control, unaffected by any of this.
- **Separated in the HR UI, structurally.** `AttemptProctoringSummary.tsx`
  filters `gaze_away` out of the counted-signals list by an explicit predicate,
  not by styling, and renders it in its own block headed "informational only",
  with its unreliability stated in the copy the reviewer actually reads. A test
  goes red if it is ever folded back in.
- **Detected on-device.** No frame, image or landmark array leaves the browser
  or is stored; only the event rows. So this creates no biometric dataset, and
  the erasure/retention/residency obligations that would follow from one do not
  arise.

**What is NOT true.** It is not true that low weighting makes the signal fair.
A reviewer who sees "looked away 11 times" can still draw an adverse conclusion
from it, whatever the score says and however carefully the panel is labelled —
the mitigation reduces how much the SYSTEM acts on the signal, and shapes how
it is presented, but it cannot control what a human infers. It is also not true
that we have validated accuracy across device classes, lighting, skin tones,
eyewear or assistive-technology use: we have not measured it at all, and the
threshold (`proctorLogic`'s debounce) was chosen to avoid flapping, not for
demographic parity.

**Path to closure.** Two honest routes, and the cheaper one is real: (a) turn
gaze off — set `exam_integrity_weight_gaze_away` to 0 and stop emitting the
event client-side; the rest of the module (presence, multiple faces, fullscreen,
tab, clipboard) works unchanged and loses nothing a customer bought. Or (b) keep
it and earn the confidence: measure false-positive rates across device and
accessibility conditions before it is ever weighted higher or shown more
prominently. Until (b) exists, (a) is the correct response to the first
substantiated complaint — that is the decision recorded here, not a preference.

---

## Index

| ID | Risk | Source | Owner | Fires when |
|---|---|---|---|---|
| **AR-1** | Demo tier is not India-resident | DPDP-3 | `platform_owner` | Residency-asserting bid, or Bedrock Mumbai approval |
| **AR-2** | **PARTIAL 2026-09-28** — RS256 capability shipped, HS256-only in every live deployment, one HS256 signer outside data_gateway remains by design | SEC-2 / SEC-1 | `security-auditor` | Fifth verifier, secret exposure, Tier-2, or the rollout actually being run |
| **AR-3** | Candidate code executes on JDoodle | AG-05 | `platform_owner` | Residency bid, confidential-IP customer, or free-tier exhaustion |
| **AR-4** | No production avatar gate; `custom` unimplemented | AG-06 residue | `cto-architect` | Production `APP_ENV`, residency bid, or 2026-11-28 sunset review |
| **AR-5** | **CLOSED 2026-09-26** — decision rationale, ledger reason and three related fields are now redacted on erasure | PH4 Wave 1 M4(b) | `platform_owner` (+ `security-auditor`) | — (fixed; kept for citations, see the entry) |
| **AR-6** | Preboarding documents, task artifacts, materials and the corpus are allow-listed, not malware-scanned | PH4 D4-3, extended PH4-D4, PH5-E2 | `platform_owner` (+ `security-auditor`) | A scanning requirement, in-app rendering or processing, a malicious-file report, or Tier-2 |
| **AR-7** | Portfolio external links are validated and stored, never fetched server-side | PH4-D4 | `platform_owner` (+ `security-auditor`) | Server-side link preview, a phishing/malware report, or a stricter allow-list requirement |
| **AR-8** | **NARROWED 2026-09-28** — erasure now finds and flags a candidate's name inside an HR-uploaded corpus document, but still cannot remove it | PH5-E2 | `platform_owner` (+ `security-auditor`) | Erasure-into-documents REMOVAL requirement, a flagged document confirmed to contain candidate data, or auto-ingested candidate content |
| **AR-9** | Gaze detection flags candidates for looking away; weighted lowest, never decisive, never validated for accuracy | Camera proctoring 2026-09-29 | `platform_owner` (+ `product-manager`) | A gaze/accessibility complaint, a request to weight it higher or rank by it, a false-positive pattern, or DPDP biometric guidance |
