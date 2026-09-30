# Camera-proctoring contract

> **What this document is, and how it came to be written late.**
>
> **42 comment lines across 18 files** in this repo cite a "camera-proctoring
> contract" **by section number** — `contract §3`, `contract §7 item 1`, and so
> on; **53 lines across 22 files** refer to the document at all, counting the ones
> that name it without a section. Until 2026-09-30 no such document existed. The specification was agreed while the
> feature was built, cited in the code as though it were a file, and never
> committed. Every one of those 53 citations was a dead footnote: a comment
> deferring its authority to something a reader could not read.
>
> This document is that specification, **reconstructed from the citations and
> then verified line by line against the implementation.** It is not a fresh
> design. Where the code and the original wording disagreed, the code is what
> shipped and this file says what the code does — the same rule CLAUDE.md
> applies to every other document here.
>
> **§6 is not reconstructable.** No comment anywhere cites it. Its subject is
> unknown, it is deliberately not guessed, and the numbering gap is left visible
> rather than closed by renumbering — renumbering would silently invalidate every
> existing citation.
>
> **Status:** describes the shipped implementation as of 2026-09-30.
> **Scope:** exam proctoring only (`exam_attempts`, `exam_integrity_events`,
> `POST /exam/integrity-event`). The AI interview has its own camera path which
> shares the consent type and the detection module but not this contract.
> **Related:** `docs/DATA-FLOW.md` (the residency and sub-processor record, which
> repeats §4's guarantee), `docs/ACCEPTED-RISKS.md` AR-9 (gaze inference).

---

## §1 — Event vocabulary and submission

**Seven event types, and no others.** The server refuses an unknown
`event_type` at the request model (`extra="forbid"`) and again at a database
CHECK.

| Type | Shape | Emitted by |
|---|---|---|
| `fullscreen_exit` | instantaneous | browser listener |
| `tab_blur` | instantaneous | browser listener |
| `copy` | instantaneous | browser listener |
| `paste` | instantaneous | browser listener |
| `face_absent` | **ranged** (`started_at`/`ended_at`) | camera |
| `multiple_faces` | **ranged** | camera |
| `gaze_away` | **ranged** | camera |

Source of truth: `exam_camera.KNOWN_EVENT_TYPES`, composed of
`INSTANT_EVENT_TYPES | RANGED_EVENT_TYPES`. `CAMERA_EVENT_TYPES` is a separate
name from `RANGED_EVENT_TYPES` even though the two sets are identical today,
because "ranged" and "needs a camera" are different ideas and a future
instantaneous camera check must gate on the latter.

**A ranged event counts once per debounced occurrence**, not once per detector
tick. The client's `proctorLogic` state machine decides when an occurrence has
happened; the server counts what it is sent.

**Submission is best-effort and must never break the exam.** Events go to
`POST /exam/integrity-event` authenticated by the candidate's exam magic link.
`sendIntegrityEvent` swallows any failure and returns `null`.

**That swallowing is why the client carries its own copy of the vocabulary.**
A 422 from an unrecognised `event_type` is indistinguishable from a dropped
packet, so a server-side vocabulary change can silently stop recording a whole
signal. It has happened once: migration `a3c5e7f9b1d4` narrowed the vocabulary
to five names without checking what the client already sent, and every
`copy`/`paste` post 422'd silently on every exam, camera-proctored or not.
`useExamProctor.KNOWN_EVENT_TYPES` now mirrors the server set and anything
outside it is dropped locally with a console diagnostic
(`warnUnsupportedEventType`) instead of being posted. **The two sets are kept in
step by hand** — there is no shared codegen — but drift now fails loudly in the
browser instead of vanishing.

---

## §2 — Severity weighting and the rolling score

```
integrity_score = max(0, 100 − Σ (weight[event_type] × count[event_type]))
```

Computed in `exam_camera.score_from_counts` from persisted counts, never folded
in incrementally. Weights come from settings, so a retune is a config change:

| Event | Weight | Counts as a violation? |
|---|---|---|
| `multiple_faces` | 25 | yes |
| `face_absent` | 20 | yes |
| `fullscreen_exit` | 15 | yes |
| `tab_blur` | 15 | yes |
| `paste` | 10 | **no** |
| `copy` | 5 | **no** |
| `gaze_away` | **5 — the lowest, and it must stay the lowest** | **no** |

An event type the function does not recognise contributes no penalty (defence in
depth only; the endpoint never stores one).

**`gaze_away` is weighted lowest deliberately and permanently.** It is the least
reliable signal and the most likely to fire on someone thinking, on a motor or
visual difference, on assistive technology, on a screen reader speaking, or on a
room with a window. Raising it to "improve" the score is a change to the
accepted risk in `docs/ACCEPTED-RISKS.md` AR-9, not a tuning decision.

**Clipboard events are scored but are never violations.** Pasting brings outside
content in and outweighs copying, which takes content out — but neither is
evidence of leaving the exam environment the way tabbing away or exiting
fullscreen is, so neither can force an auto-submit on its own.
`VIOLATION_EVENT_TYPES` is the enforcing set.

**The auto-submit threshold is a client-side courtesy, not a control.**
`exam_integrity_max_violations` (default 3) is *returned* to the browser, which
calls `onAutoSubmit()` once when the count reaches it. A malicious client
ignores it. Flooding with violations is not a useful attack for a different
reason: it records every one of them and a score of 0 against the flooder,
trading one concealed `multiple_faces` for the most damning timeline they could
have produced. **That is a property of the data, not an enforcement point** —
stated here because a reader looking for the enforcement point will not find
one.

---

## §3 — Consent: the round's own setting, and a step of its own

**Two independent gates, and both must be satisfied.**

1. **The company's setting on the round** —
   `exam_rounds.camera_proctoring_required`. Editable even after the round is
   published or taken, because it is a delivery setting rather than a grading
   one; it is therefore *not* frozen by `exam_rounds_frozen()`.
2. **This candidate's own active consent** — type `video_capture`, purpose
   `interview`, notice version `1`, in `dpdp_consent_ledger`. The same
   type/purpose pair the AI interview's camera consent uses, so granting it once
   covers both doors and the ledger holds one active row per
   (user, type, purpose).

`GET /exam` returns both facts so the client knows whether to show the consent
step or skip straight past it.

**Consent is its own screen, never bundled with anything else.** DPDP §6(1):
consent is freely given or it is not consent. `CameraConsentModal` is a separate
step from the exam's recording/scoring consent checkbox on the intro card. It
states, in plain words and *before* the candidate decides: that the camera stays
on for the whole exam, what is detected, that no video or image is ever recorded
or sent anywhere, and that only the detection events are stored. It is a real
dialog — `role="dialog"`, `aria-modal`, focus trapped, Escape declines.

**Declining is not a dead end.** `POST /exam/start` returns **422** when the
round requires the camera and no active consent exists, checked *before* the
attempt row is created so a declined candidate never gets one. The frontend then
shows a screen saying why and what to do, with a "reconsider" path back into the
consent step. Declining is the candidate's call to make, not a one-way door.

**Agreeing in the modal does not itself write the ledger.** `onAgree` is a
synchronous local state flip that closes the modal; the grant is recorded at
Start. (This was corrected in code review on 2026-09-29 — the modal's own
docstring had claimed otherwise.)

**Acceptance is evidenced in `audit_log`, at most once per round.** Because the
second door mints no new ledger row, the acceptance itself is recorded as
`exam.camera_notice.accepted`, carrying the notice version, the applicant, the
round, the grant and the timestamp. Written with `ON CONFLICT DO NOTHING`
against the partial unique index `ix_audit_log_camera_notice_round` — never a
SELECT probe (which sequentially scanned a permanently-growing table from an
unauthenticated route, and was TOCTOU), and never `DO UPDATE` (`audit_log` is
append-only by trigger and would raise).

**Read that row's meaning precisely.** It evidences that an acceptance POST
bearing that candidate's exam link reached the server at that time, and which
notice version was being served. It is **not** a client attestation that the
modal rendered, it is **not** tied to the round actually being started, and the
exam link is a **bearer credential** — the row names the candidate the link was
issued to. It deliberately carries no request metadata, not even a salted IP
hash: one global salt over a 2³² address space is enumerable, which would make
it pseudonymous personal data retained forever in a table erasure cannot reach,
for no purpose the row serves.

**`camera_in_use` records the requirement, not the outcome.**
`exam_attempts.camera_in_use` is frozen at `/exam/start` from
`camera_proctoring_required` **alone** — no consent lookup, and nothing anywhere
sets it from a browser signal. A round that does not ask for the camera never
turns the pipeline on, even for a candidate who separately holds a
`video_capture` consent from an earlier interview. Consequently `true` with no
camera events is **genuinely ambiguous** between a candidate present throughout
and a camera that never started, and §7 item 4 requires the HR panel to say so.

---

## §4 — No frame ever leaves the browser, and the candidate can see the camera is on

**The hard guarantee: no image, video frame or landmark array is ever captured,
transmitted or stored.** Face and gaze detection run entirely in the candidate's
browser via MediaPipe `@mediapipe/tasks-vision`, in a module worker, from
**self-hosted same-origin assets** (`/mediapipe/wasm`,
`/mediapipe/face_landmarker.task`, vendored by
`web/scripts/fetch-mediapipe.mjs`) — **no runtime CDN call**. Only the derived
`{type, started_at, ended_at}` event crosses the wire.

This is enforced structurally, not by convention. `extractGazeSignals` is the
**one** function that ever touches a raw landmark or frame; everything
downstream (`advanceCondition`, `closeOpenConditions`) handles only the ranged
event shape. `proctorLogic.test.ts` asserts both properties against the source,
not merely against the types.

This guarantee is also stated in `docs/DATA-FLOW.md`, which is the record we can
be asked to substantiate. Weakening it is a change to that document and to every
consent notice that repeats it.

**The candidate must be able to see the camera is on.** Otherwise it is
surveillance rather than proctoring. Two pieces:

- **A local self-view (PiP)**, never published anywhere, mirrored so it behaves
  like a mirror. Kept in the DOM whenever the camera is the active signal path —
  even before the stream attaches — so the detector always has a live element.
- **`ProctorIndicator`**, which says what the system currently sees:
  `unavailable` → `starting` → `calibrating` → `warning` → `ok`, resolved in
  that priority order by the pure, separately-tested
  `proctorIndicatorStatus`. Every kind of camera failure collapses to the same
  `unavailable`, and the exam continues.

**The indicator never reports anything that reads as an accusation.** Warning
copy mirrors the interview's neutral nudges ("please look at the screen"), never
a verdict. `role="status"` with `aria-live="polite"` so screen-reader users get
the same information without being interrupted.

No frames are emitted during the 2.5 s calibration window, which establishes the
candidate's own facing-forward pose and kills false positives from off-angle
seating.

---

## §5 — Accommodations relax camera proctoring through the existing mechanism

An accommodation that relaxes auto-submit (PH4-D2 `relax_auto_submit`, frozen
onto the attempt as `auto_submit_relaxed`) **must relax the camera signals too,
through the same path as the browser events — never a parallel one.**

Mechanically: camera events are submitted to the same
`POST /exam/integrity-event` and counted by the same
`VIOLATION_EVENT_TYPES` / `_max_violations_for` logic as `fullscreen_exit` and
`tab_blur`, so the relaxation applies automatically. `_max_violations_for`
returns `None` for a relaxed attempt and the client then never auto-submits on
count. The value is read from the **attempt**, which froze the allowance at
`/start`, so revoking the accommodation mid-attempt cannot shorten a clock the
candidate has already been shown.

`useExamProctorCamera.test.tsx` proves a relaxation learned from a *camera*
event reaches the pre-existing browser-event handling, rather than only its own
code path. A second, parallel relaxation mechanism is the failure this section
exists to prevent: it would pass its own tests while leaving the accommodation
half-applied.

---

## §6 — [not reconstructable]

**No code comment anywhere in the repository cites §6.** Its subject is unknown
and is deliberately not guessed here.

This gap is left visible on purpose. Inventing a plausible section would put an
unagreed rule into a document that gets cited in compliance answers and bid
responses, which is exactly what CLAUDE.md's documentation rule forbids
("when you cannot verify a claim, write *unverified* — never a plausible
value"). Renumbering §7 and §8 to close the gap would silently invalidate the 13
citations that name them.

If the original specification resurfaces, fill this in. Until then the gap *is*
the honest record.

---

## §7 — The HR review panel

Surfaced by `GET /hr/exams/{examId}/attempts/{attemptId}/proctoring`, rendered
by `AttemptProctoringSummary` on the attempt detail page.

**The rule the whole section exists for:** a score tells HR nothing about *why*
it dropped, and a reviewer under time pressure is exactly the person most likely
to pattern-match "low score = cheated" regardless of which events produced it.
Every item below follows from that.

**Item 1 — `gaze_away` is its own clearly-labelled informational block**,
separate from the counted signals, and **must never be folded back in** however
tempting a single "all events" list is. Merging it would let a reviewer
reconstruct precisely the read-through the low weighting was designed to
prevent. It triggers nothing by itself and is shown for context only, with the
reasons it is unreliable stated next to it. It renders only when the camera was
in use, since it cannot otherwise exist.

**Item 2 — the panel leads with what happened and puts the score last.** Events
first, grouped by kind, with times and durations; the score afterwards as a
summary of them, never as the headline.
*(No citation names §7 item 2 by number. This is reconstructed from the panel's
own first documented rule, which is the only §7 obligation not accounted for by
items 1, 3, 4 and 5. Treat the numbering as probable rather than attested.)*

**Item 3 — never state or imply a conclusion.** No "suspicious", no verdict
styling, no pass/fail colouring, no per-candidate ranking. This informs a human;
it does not decide (CLAUDE.md hard constraint 9).

**Item 4 — when the camera was not in use, say so in words** rather than
rendering the camera signals as a misleadingly clean-looking zero. The server
omits camera keys from `counts` entirely in that case, so there is no zero to
render.

Since 2026-09-30 this extends to the other direction, which is the harder case:
when the round **required** the camera and produced **no camera signal at all**,
the panel states the ambiguity explicitly — a candidate present throughout and a
camera that never started (permission declined, detector failed to load) are
indistinguishable in this record, and §3 explains why. A single `gaze_away`
occurrence counts as a camera signal for this purpose: it is informational and
never a violation, but it is still proof the detector ran.

**Item 5 — show real durations.** Seconds and minutes are different facts:
`6 → "6s"`, `65 → "1m 5s"`, `120 → "2m"`. Never a tick mark.

**An incomplete timeline must say it is incomplete.** The rate limiter can
refuse an event, and the client swallows a 429 exactly like a lost packet, so
without this the only trace would be a Prometheus counter nobody joins to an
attempt — and a short record reads as a clean one. `events_dropped` on
`proctoring_summary` drives a notice worded as a fact about *our collection*,
never about the candidate: being throttled is not something they did.

That flag is **sticky**. `exam_camera.rolling_summary` recomputes everything from
the persisted counts except `STICKY_SUMMARY_KEYS`, which it carries forward. A
recount must never be able to un-say "this record is incomplete" — and it used
to, because the ingest endpoint rebuilt the dict on every accepted event.

**The list view deliberately shows neither the score nor the counts.** The API
carries `camera_in_use` and `integrity_score` so a caller *can* tell "not
watched" from "watched and clean" without opening every attempt, but a bare
score in a scannable table is exactly where the pattern-match happens, with none
of the context that makes the per-attempt panel honest. **If a list-level
indicator is ever wanted, show camera on/off — never the score alone.**

---

## §8 — Localisation of the consent copy

Every `publicExam.camera*` key sits on the **higher-bar list** at the top of
`web/src/lib/i18n.ts`, alongside the erasure keys, the `ConsentModal` keys and
the rediscovery opt-in.

The reasoning: the blanket "HI/TE are a first pass, review before a production or
government-bid launch" caveat is a whole-bundle, pre-launch ask — too slow and
too coarse for copy a candidate makes an irreversible decision on. A
mistranslated button label is an annoyance; a mistranslated camera-consent
notice is a candidate consenting to something they did not understand, which is
not consent under DPDP §6(1) at all.

All three Day-1 languages must carry every key. **HI and TE ship unreviewed** —
they need native-speaker and, for the consent copy, legal review before a
production or government-bid launch. That is an open gap, not a completed step.

Enforced mechanically since 2026-09-30 by
`web/src/__tests__/i18nLocaleParity.test.tsx`, which checks parity in both
directions, plural suffixes, and interpolation placeholders. The mechanism exists
because i18next is configured with `fallbackLng: 'en'`, which makes a missing
translation indistinguishable from a present one at the call site — and five
candidate-facing exam strings had been served in English that way, three of them
because HI and TE spelled a counted key without i18next's `_one`/`_other`
suffix, which is never looked up.

---

## Citation index

Where each section is cited from, as of 2026-09-30, counted by
`services/data_gateway/tests/unit/test_camera_proctoring_contract.py` — which
fails if code cites a section this document does not define, **or** if this
document defines one nothing cites.

The column below counts **section mentions** (44), which is two more than the 42
lines carrying a citation: one line writes the compound `§3/§8`, and one repeats
a citation inside a `describe()` directly under the comment that made it.

| § | Citations | Principal files |
|---|---|---|
| §1 | 3 | `useExamProctor.ts`, `useExamProctorCamera.test.tsx` |
| §2 | 4 | `AttemptProctoringSummary.tsx`, `ProctorIndicator.tsx` |
| §3 | 16 | `exam_take.py`, `PublicExam.tsx`, `CameraConsentModal.tsx`, `hr_rounds.py`, `publicExam.ts`, `i18n.ts` |
| §4 | 6 | `proctorLogic.test.ts`, `PublicExam.tsx`, `ProctorIndicator.test.tsx`, `useExamProctor.ts` |
| §5 | 2 | `useExamProctor.ts`, `useExamProctorCamera.test.tsx` |
| **§6** | **0** | — |
| §7 | 12 | `AttemptProctoringSummary.tsx`, `hr_exams.py`, `ExamAttemptDetail.tsx`, `exams.ts` |
| §8 | 1 | `i18n.ts` (written `§3/§8`, so a search for `contract §8` alone finds nothing) |

**One near-miss worth knowing about.** `web/src/api/rediscovery.ts` also said
"contract §3", meaning the **PH5-E3 rediscovery** specification, not this one. It
now names that contract explicitly. If you are counting citations, that file is
not one of them.
