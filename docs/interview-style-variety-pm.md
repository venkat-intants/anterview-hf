# Interview Style Variety — PM Specification
**Document:** interview-style-variety-pm.md v1.0
**Date:** 2026-05-27
**Audience:** CTO, ai-orchestrator agent
**RFP anchor:** ITC51-14022/9/2026 — Pg 9 "Adaptive by experience tier"; Pg 8 "Each avatar have a unique visual appearance"

---

## Problem Statement (verbatim user signal)

> "I feel like all interviews are going in the same pattern... every time it will be dynamic."

Current behavior: two sessions on the same job_role feel identical. Same opening, same competency order (INTRO → TECH_Q → BEHAV_Q → CAND_Q → CLOSE), same warmth level, same difficulty arc. The user is correct — the system delivers one house style.

---

## RFP / HLD Grounding

The RFP explicitly requires:
- Pg 9: "Adaptive by experience tier" — already handled via `experience_tier` field
- Pg 8: 6 distinct avatars (3M/3F) with unique visual appearance — avatar visual variety is already implemented
- Pg 9: Interview structure is defined as Introduction → Technical/Domain → Behavioral → Candidate Questions → Conclusion — this sequence is FIXED by the RFP

The RFP does NOT specify persona variety, conversational warmth levels, or difficulty arcs beyond the experience tier. This means we have design freedom in how sessions feel, as long as the RFP-mandated structure is maintained.

---

## 1. Variety Dimensions — What Should Actually Vary

### APPROVED dimensions (low-cost, high-impact, in-scope)

**A. Opening question type (Q1 variation)**
Currently always "Tell me about yourself." This is the most obvious repetition signal.
Q1 can vary without schema changes — it is just a prompt instruction.
Options:
- Classic: "Tell me about yourself"
- Role-first: "What draws you to this specific role?" (skips autobiography, dives to motivation)
- Competency-first: "Walk me through a project where you used [top skill from JD]" (for mid/senior tiers)

**B. Competency coverage order within TECH_Q**
Currently the LLM rotates across 4 competencies in a fixed sequence. The order can be shuffled per-session using the `asked_questions` set (already in Redis and state). No schema change needed.

**C. Conversational warmth / pacing style**
Currently `persona.style` and `persona.tone` exist in the `Persona` TypedDict and the `interviewer_system.j2` prompt, but they are hardcoded to the avatar. These can be varied at session-init time with a `style_profile` that is selected based on avatar + a session-level variation seed.

**D. Difficulty arc**
Currently difficulty is flat within the experience tier. We can add slow-ramp vs fast-dive as a prompt instruction to `interviewer_system.j2`.
- Slow ramp: start with foundational/recall questions, progress to applied/reasoning
- Fast dive: open with an applied scenario question immediately after intro

### NOT APPROVED for this release (see Section 5)

Phase order changes, adversarial styles, candidate-choosable styles.

---

## 2. MVP Scope — 4 Interview Styles

These 4 styles can be implemented entirely as prompt-level variations plus a session-init selection. No new DB tables, no new API endpoints, no new LangGraph nodes. The only schema touch is adding `interview_style` to the `sessions` table metadata JSONB (already exists) and to `InterviewState`.

### Style 1: "Warm Ramp" (default for freshers)
- Q1 type: Classic autobiography ("Tell me about yourself")
- Difficulty arc: Slow — foundational knowledge first, applied later
- Warmth: High — frequent acknowledgment ("Good point, tell me more...")
- Competency order: Shuffled randomly from NOS list
- Avatar affinity: hr avatars (Arjun, Priya) preferred but not locked
- RFP tier: fresher primary, mid secondary

### Style 2: "Direct Dive" (default for mid-level)
- Q1 type: Role-first ("What specifically draws you to this role?") or Competency-first (weighted 50/50)
- Difficulty arc: Fast — opens with a real applied scenario in first TECH_Q turn
- Warmth: Moderate — professional acknowledgment, no extended affirmations
- Competency order: Anchored to top 2 NOS skills from JD, then breadth
- Avatar affinity: tech_lead avatars (Rohan, Lakshmi) preferred
- RFP tier: mid primary, senior secondary

### Style 3: "Strategic Probe" (default for senior)
- Q1 type: Competency-first — opens on a leadership or decision scenario
- Difficulty arc: Sustained high — no warmup, assumes competence, probes edge cases
- Warmth: Low-moderate — terse acknowledgments, quick follow-ups ("And how did that turn out?")
- Competency order: Depth-first — pick one competency and drill 2-3 layers before moving
- Avatar affinity: exec avatars (Vikram, Anjali) preferred
- RFP tier: senior primary

### Style 4: "Conversational Explorer" (rotates across all tiers, adds entropy)
- Q1 type: Rotated — system picks from all three Q1 types, avoiding whatever the candidate saw last session
- Difficulty arc: Adaptive — starts moderate, rises if candidate responds confidently (LLM-driven via prompt instruction, no new tool needed)
- Warmth: Moderate-high — storytelling encouraged ("Can you paint me a picture of that situation?")
- Competency order: Breadth-first — touches all NOS competencies briefly before deepening any
- Avatar affinity: any avatar, cross-role
- RFP tier: all tiers

---

## 3. Selection Logic

**Rule: style is system-selected at session-init, not candidate-chosen.**

Rationale: candidate-choosable styles break scoring fairness. If one candidate picks "Warm Ramp" and another picks "Strategic Probe" for the same job, the 4-axis scores are not comparable. APSSDC cohort analytics depend on comparable scores across candidates in the same district/batch.

**Selection algorithm (in `hydrate_context` node):**

```
1. Look up candidate's last session for this job_role (Redis or DB)
2. Determine eligible styles:
   - fresher → pool: [Warm Ramp, Conversational Explorer]
   - mid     → pool: [Direct Dive, Conversational Explorer]
   - senior  → pool: [Strategic Probe, Direct Dive, Conversational Explorer]
3. Exclude the style used in the immediately preceding session (anti-repeat)
4. Random-select from remaining eligible pool
5. Store selected style in session.metadata JSONB as {"interview_style": "warm_ramp"}
```

This gives:
- Freshers: 2 styles, guaranteed different on back-to-back sessions
- Mid: 2 styles, same guarantee
- Senior: up to 3 styles in rotation
- No session ever feels identical to the previous one for that candidate

Cost: one Redis read (last session style per user+job_role key). Zero additional LLM calls. Zero new DB columns needed (uses existing `metadata JSONB`).

---

## 4. What Must NOT Vary — Fairness and Compliance Locks

These are hard constraints. The ai-orchestrator must treat these as invariants across all 4 styles.

### Phase sequence is FIXED
`INTRO → TECH_Q → BEHAV_Q → CAND_Q → CLOSE`

The RFP (Pg 9) explicitly defines this structure. We cannot reorder phases. "Strategic Probe" does NOT skip BEHAV_Q. "Warm Ramp" does NOT extend INTRO beyond its phase limits.

Phase time limits in `transitions.py` (`PHASE_LIMITS`) are FIXED regardless of style. A "Direct Dive" style does not get extra TECH_Q time.

### Scoring rubric is FIXED
The 4 axes — Communication, Technical, Problem Solving, Confidence — and their calibration anchors (0-3, 4-5, 6-7, 8-9, 10) are identical across all styles. The scorer prompt (`scorer.j2`) receives no style information. Scores must be directly comparable across sessions.

### NOS/NSQF competency anchoring is FIXED
All styles must cover the NOS competencies for the job role. Coverage ORDER may vary. Coverage COMPLETENESS does not. Scoring fairness requires that every session touched the same competency domains.

### Language rules are FIXED
All styles respond only in the candidate's chosen language. No style introduces code-switching. No style switches to English for "sophistication."

### Hard limits on question sensitivity are FIXED
The 10 hard rules in `interviewer_system.j2` (no PAN/Aadhaar, no political content, no revealing AI identity, etc.) apply to all styles without exception.

### Utterance length cap is FIXED
40-word cap per AI utterance applies to all styles. "Conversational Explorer" is warmer, not wordier.

### DPDP compliance hooks are FIXED
`dpdp_consent_ledger` entry requirement, audio retention, erasure rights — none of these are style-dependent.

---

## 5. Out-of-Scope Rejections

**REJECTED: Adversarial / stress interview style**
Verdict: REJECTED
Reason: This is a mock-interview screening tool for government skilling (APSSDC) and private engineering college candidates who are largely freshers. An adversarial style causes distress, increases dropout, produces unusable scores (confidence axis collapses), and risks APSSDC reputational damage. Not in RFP. Not requested by any real user cohort.

**REJECTED: Candidate-choosable style at session start**
Verdict: REJECTED
Reason: Destroys scoring comparability. A candidate choosing "Warm Ramp" vs "Strategic Probe" on the same job role will produce incomparable Communication and Confidence scores. APSSDC cohort analytics are the primary value-add for the govt contract. Breaks that value entirely.

**REJECTED: Per-session difficulty slider (admin-configurable)**
Verdict: REJECTED
Reason: Gold-plating. Adds an admin UI panel, a new config field, session-start logic, and a new dimension of scoring variance. The experience_tier field already handles the primary difficulty calibration the RFP requires.

**REJECTED: "Rapid fire" mode (short Q&A, high turn count)**
Verdict: REJECTED
Reason: Phase time limits exist for a reason — they bound session cost. A rapid-fire mode with more turns increases LLM token count and TTS duration, threatening the ₹12/session cost ceiling. Not in RFP.

**REJECTED: Style persistence across sessions (the system "learns" your preferred style)**
Verdict: REJECTED
Reason: Personalization engine. Large ongoing ML cost. Not in RFP. Session variety is sufficient without building a preference model.

**REJECTED: Different avatar per style (style forces avatar choice)**
Verdict: REJECTED — partial
Clarification: Avatar "affinity" (preferred pairing) is a prompt hint only. The candidate's avatar choice at session-start remains theirs. We do NOT force Rohan on "Direct Dive" sessions. We may seed the LLM persona instruction differently, but the visual avatar is candidate-selected.

**REJECTED: New language as a "style" dimension**
Verdict: REJECTED
Reason: Adding Tamil/Kannada/etc. as a style is not variety — it is a language expansion milestone. EN/HI/TE must be rock-solid before any expansion. Adding languages is a separate workstream gated on Bhashini TTS quality for those languages.

---

## 6. Implementation Touchpoints for CTO / ai-orchestrator

All changes are isolated to `interview_core`. No changes to `data_gateway`, `feedback_billing`, or `admin_ops`.

### Files to touch

**`services/interview_core/graph/state.py`**
Add `interview_style` field to `InterviewState`:
```python
interview_style: Literal["warm_ramp", "direct_dive", "strategic_probe", "conversational_explorer"]
```

**`services/interview_core/graph/nodes.py`**
In `hydrate_context` node: add style selection logic (pool lookup, anti-repeat, random select). Write selected style to Redis key `session:{session_id}:style` and to state.

**`services/interview_core/prompts/interviewer_system.j2`**
Add style-conditional block after the persona section:

```jinja
## Interview Style: {{ interview_style }}
{% if interview_style == "warm_ramp" %}
- Open with a classic self-introduction request.
- Start TECH_Q with foundational recall questions before applied scenarios.
- Use warm acknowledgments frequently ("That's a good point, tell me more...").
{% elif interview_style == "direct_dive" %}
- Open with role-motivation or competency-first question (pick one).
- Open TECH_Q immediately with a real applied scenario question.
- Use brief professional acknowledgments, not extended affirmations.
{% elif interview_style == "strategic_probe" %}
- Open with a leadership or decision scenario question.
- Maintain high difficulty throughout; do not scaffold down for hesitation.
- Probe 2-3 layers deep on one competency before moving to the next.
- Use terse acknowledgments ("And then?", "What was the outcome?").
{% elif interview_style == "conversational_explorer" %}
- Rotate Q1 type randomly (avoid what this candidate saw last session).
- Start moderate difficulty, increase if candidate responds with depth and confidence.
- Encourage storytelling ("Can you walk me through that situation?").
- Cover all NOS competencies in breadth before deepening any one.
{% endif %}
```

**`services/interview_core/prompts/intro.j2`**
Add style-conditional Q1 instruction:
```jinja
{% if interview_style == "warm_ramp" or interview_style == "conversational_explorer" %}
- End with: ask them to introduce themselves briefly.
{% elif interview_style == "direct_dive" %}
- End with: ask what specifically draws them to this role at this point in their career.
{% elif interview_style == "strategic_probe" %}
- End with: ask them to walk you through a high-stakes decision they made recently.
{% endif %}
```

**`services/interview_core/graph/transitions.py`**
No changes needed. Phase limits are invariant.

**`services/interview_core/prompts/scorer.j2`**
No changes. Style is invisible to the scorer.

### New Redis key
`session:{session_id}:style` — String, 30 min sliding TTL. Used for anti-repeat lookup on next session.

### New sessions metadata field
`metadata JSONB` already exists on the `sessions` table. Store `{"interview_style": "warm_ramp"}`. No DDL change needed.

---

## 7. Cost Impact Assessment

| Change | LLM tokens delta | TTS delta | Infra delta |
|---|---|---|---|
| Style instruction in system prompt | +~80 tokens per session (cached) | None | None |
| Style selection logic in hydrate_context | 0 LLM calls | None | 1 Redis read |
| Anti-repeat Redis key | 0 LLM calls | None | Negligible |
| **Total per-session cost delta** | **+₹0.05 max** | **₹0** | **₹0** |

Well within ₹12/session ceiling.

---

## 8. Recommendation: Ship / Defer / Reject

### SHIP (Phase 1, estimated dev=S)
- 4 interview styles as defined in Section 2
- Style selection logic in `hydrate_context` (anti-repeat, tier-constrained pool)
- Prompt modifications to `interviewer_system.j2` and `intro.j2`
- `interview_style` field in `InterviewState` and session `metadata`
- Anti-repeat Redis key

Implementation estimate: 1 engineer, 1.5 days. No new services, no schema migrations, no new API endpoints.

### DEFER (Phase 2 or later)
- Admin dashboard filter by interview_style (useful for APSSDC analytics once data accumulates, but not Day-1 critical)
- Expanding to 5-6 styles as real session data reveals which styles produce completion-rate and score-variance differences

### REJECT (permanently, unless RFP changes)
- Adversarial/stress style
- Candidate-choosable style
- Per-session difficulty slider
- Style persistence/personalization engine
- Rapid-fire mode
- Language expansion as a style dimension

---

*End of document. Review by code-reviewer agent required before implementation begins.*
