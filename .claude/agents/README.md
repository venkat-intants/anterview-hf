# Intants AI Agent Team

This folder defines the **11-agent specialized team** that builds and maintains the Intants AI Voice Interview Platform.

Each agent is a Markdown file with frontmatter (name, description, tools, model) and a focused system prompt. Claude Code loads these automatically and routes work to the right specialist based on the `description` field.

---

## The Team

### Strategic (3) — Advise, never auto-execute big decisions

| Agent | Role | Model | When to invoke |
|---|---|---|---|
| `product-manager` | Keeps work aligned with RFP + product vision; rejects scope creep | sonnet | Before adding any new feature |
| `cto-architect` | Enforces simplicity; prevents over-engineering | opus | Before architectural decisions |
| `cfo-cost-watcher` | Tracks API spend; protects per-session unit economics | haiku | Weekly + before adopting paid services |

### Build (4) — Do actual coding work

| Agent | Role | Model | Owns |
|---|---|---|---|
| `backend-engineer` | Python / FastAPI / LangGraph code | sonnet | `services/`, `shared/` |
| `frontend-engineer` | React / TypeScript / Three.js code | sonnet | `web/` |
| `ai-orchestrator` | Prompts, LangGraph nodes, voice pipeline, scoring | opus | `prompts/`, `evals/`, voice adapters |
| `devops-engineer` | Docker / Kubernetes / CI/CD / observability | sonnet | `infra/`, `.github/workflows/` |

### Review (2) — Gate quality before merging

| Agent | Role | Model | Triggered by |
|---|---|---|---|
| `security-auditor` | OWASP, DPDP, secrets, CVEs (white-hat ONLY) | opus | Before every prod deploy + on auth/PII changes |
| `code-reviewer` | Bug catching, test coverage, style consistency | sonnet | Every PR before merge |

### Watchdog (1) — Background monitoring

| Agent | Role | Model | Schedule |
|---|---|---|---|
| `market-researcher` | Bhashini/Bedrock pricing, competitor moves, new RFPs | sonnet | Weekly (cron) |

### Coordination (1) — Keep delivery on track

| Agent | Role | Model | Owns |
|---|---|---|---|
| `sprint-coordinator` | Sprint planning, daily progress, blockers, weekly reports, backlog | sonnet | `sprints/`, `roadmap.md` |

---

## How to Invoke an Agent

In Claude Code, just describe what you need. The harness picks the right agent based on its `description` field. You can also force-call a specific agent:

```
Use the cto-architect agent to review this microservice split proposal.
Use the security-auditor agent to scan the auth module before merge.
```

For parallel work, request multiple agents in one message — they run concurrently.

---

## Roster Changes

To add an agent: create `<agent-name>.md` here with the standard frontmatter. To retire one: move to `.claude/agents/_retired/` (don't delete — keep history).

Current roster locked at **11**. Adding a specialist (e.g., `qa-engineer`, `data-engineer`, `docs-writer`) requires `cto-architect` approval.

---

## Hard Rules All Agents Follow

1. Read `CLAUDE.md` for project context before any work
2. Read relevant section of `HLD.md` / `LLD.md` before designing
3. Never use a tech not in `Final_stack.md`
4. Never hardcode secrets — `.env` only
5. Never bypass `code-reviewer` for production code
6. Never bypass `security-auditor` for prod deploys
7. Report findings tersely — no fluff

---

## Anti-Patterns (don't do these)

- ❌ Inventing a new agent for one-off tasks — use existing ones
- ❌ One agent doing another agent's job (frontend writing backend code)
- ❌ Skipping review agents to "move fast"
- ❌ Adversarial / black-hat security tooling (security-auditor is white-hat ONLY)
- ❌ Letting any agent auto-deploy to production
