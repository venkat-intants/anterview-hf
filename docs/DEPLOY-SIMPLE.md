# Deploy Guide — Railway + Vercel (HISTORICAL RECORD)

> ## ⚠️ SUPERSEDED 2026-08-07 — do not follow this guide for a deploy.
>
> **Railway is not a supported deploy target.** Everything below section 1 is a
> record of the Railway + Vercel setup as it was; it is not maintained and it is
> not verified against the app at HEAD. Its `vercel.json` rewrite routing and
> cross-origin assumptions no longer match the frontend, and nothing in CI
> exercises this path.
>
> *(Date note: 2026-08-07 is when the supersession was recorded in the tree. The
> Railway deploy itself was retired earlier — the exact date is **unverified**,
> because the repository history before 2026-07-05 was squashed at import.)*
>
> **Deploy targets that are actually current:**
>
> | Target | Guide | What it is |
> |---|---|---|
> | **Hugging Face Space** ← the live one | [`../README.md`](../README.md) | All six processes in one free Docker Space, configured by `space/entrypoint.sh` + `space/supervisord.conf` + `space/Caddyfile`, deployed by `.github/workflows/sync-to-space.yml` on every green `main` build. |
> | Self-hosted VM | [`DEPLOY-ORACLE.md`](DEPLOY-ORACLE.md) | One Oracle Cloud free VM running `docker-compose.prod.yml` behind Caddy TLS, set up by `scripts/oracle-setup.sh`. |
> | Tier-2 production | [`Final_stack.md`](Final_stack.md) | AWS Mumbai EKS. Not yet built. |
>
> Two other legacy paths exist and are also superseded: `render.yaml` /
> [`RENDER_DEPLOY.md`](RENDER_DEPLOY.md) (deprecated at `render.yaml:1-7`), and
> this document. Neither is an alternative to the table above.
>
> **Why it is kept.** The section-by-section record below is still the clearest
> statement of *what each of the five backend processes needs* — Dockerfile
> paths, the worker's start command and no-domain rule, the shared-vs-per-service
> split of environment variables, and the one-time migration step. Those facts
> carry over to any host. Read it as reference material, then deploy using one of
> the guides above.

This document explains how the project *was* put on the internet using Railway
(backend) and Vercel (frontend), in plain language, step by step.

> **On `web/vercel.json` — the Railway links are GONE, and must not come back.**
> This note used to say the file "still holds the backend links from that Railway
> deploy". It does not: at HEAD the only rewrite left is the SPA fallback
> (`/:path*` → `/index.html`), and the file carries a standing instruction not to
> re-add the per-service `/api/*` rewrites, because they pointed at retired
> Railway URLs and conflicted with the real routing (`web/vercel.json:9`). The
> frontend now calls the API **directly** via the `VITE_*_API_URL` values, so
> Section 8 step 3's `/api/...` values and step 4's "replace each
> `up.railway.app` link" are both part of the historical record — following them
> today re-introduces a routing conflict. The current targets reach the backend
> differently: on the HF Space one Caddy serves the SPA and proxies the APIs on
> the same origin, so no rewrite exists at all; on the Oracle VM the frontend
> stays on Vercel and the
> backend answers on its own `api.` domain, which is **cross-origin** and is why
> `AUTH_COOKIE_SAMESITE=none` is set in `docker-compose.prod.yml:114` (see
> [`DEPLOY-SECURITY.md`](DEPLOY-SECURITY.md)). The CSRF double-submit cookie that
> makes `SameSite=none` survivable is *not* a deploy setting — `data_gateway`
> issues it unconditionally (`services/data_gateway/app/config.py:343-357`), so
> there is nothing to add on a new host and nothing to switch off by forgetting.

---

## 1. What was deployed (in plain words)

> **How to read sections 2–12.** They are written as instructions because that
> is how they were written at the time, and rewording every sentence into the
> past tense would only make them harder to reuse. They are *not* a live
> runbook. Nothing below has been re-verified against the app at HEAD. Where a
> step names a Railway screen, the equivalent on a current target is in
> [`../README.md`](../README.md) (Space secrets) or
> [`DEPLOY-ORACLE.md`](DEPLOY-ORACLE.md) (`.env` files on the VM).

The project has **two parts**:

1. **The website** (what users see) — built from the `web/` folder. It goes on **Vercel**.
2. **The backend** (the brain + database work) — 5 small programs. They go on **Railway**.

The 5 backend programs are:

| Program | What it does |
|---|---|
| `data-gateway` | Login, users, privacy/consent |
| `interview-core-api` | Starts and manages interviews |
| `interview-core-worker` | Runs the talking avatar + voice (no web link, works in background) |
| `feedback-billing` | Scores the interview + makes the PDF report |
| `admin-ops` | Admin dashboard data |

They all share **one database** (Neon), **one cache** (Upstash), and **one file
store** (Cloudflare R2). You already have these — the keys are in the `.env` files.

```
  User's browser
       |
   [ Vercel website ]  ---->  [ Railway: 5 backend programs ]
                                      |
                        [ Neon DB ] [ Upstash ] [ R2 files ] [ LiveKit ]
```

---

## 2. Things to get ready first

Create free accounts on these (if you don't have them):

- **GitHub** — to store the code: https://github.com
- **Railway** — for the backend: https://railway.app
- **Vercel** — for the website: https://vercel.com

You also need the keys/links that are **already in your `.env` files**:
- Neon database link (`DATABASE_URL`)
- Upstash cache link (`REDIS_URL`)
- Cloudflare R2 keys (`S3_*`)
- LiveKit project (`LIVEKIT_URL`, key, secret)
- Gemini, Sarvam, Tavus/Simli keys

Keep those `.env` files open — you will copy values from them.

---

## 3. Put the code on GitHub

If the code is not on GitHub yet:

1. Make a new **private** repository on GitHub (call it `anterviewer`).
2. In a terminal inside the project folder, run:
   ```bash
   git add .
   git commit -m "ready to deploy"
   git branch -M main
   git remote add origin https://github.com/<your-username>/anterviewer.git
   git push -u origin main
   ```

Now Railway and Vercel can read your code.

> **Important:** the `.env` files have real passwords/keys. Before sharing the
> repo with anyone, see Section 9 (rotate secrets).

---

## 4. Deploy the 5 backend programs on Railway

Do this **once per program** (5 times). It sounds like a lot but each one takes
~2 minutes.

1. Go to Railway → **New Project** → **Deploy from GitHub repo** → pick your repo.
2. After it adds the first service, open the service **Settings**:
   - **Root Directory:** `/`  (leave as the repo root — do not change it)
   - **Dockerfile Path:** `services/data_gateway/Dockerfile`
3. Click **Deploy**.
4. To add the next program: in the project, click **New** → **GitHub Repo** →
   same repo → set its **Dockerfile Path** to the next one below.

Use these Dockerfile paths, one per service:

| Service name to type | Dockerfile Path |
|---|---|
| `data-gateway` | `services/data_gateway/Dockerfile` |
| `interview-core-api` | `services/interview_core/Dockerfile` |
| `feedback-billing` | `services/feedback_billing/Dockerfile` |
| `admin-ops` | `services/admin_ops/Dockerfile` |
| `interview-core-worker` | `services/interview_core/Dockerfile` |

**Special setting for the worker only** (`interview-core-worker`):
- It has **no web link**. In its Settings, turn **OFF** "Generate Domain".
- Set its **Start Command** to:
  ```
  python -m app.worker.interview_worker start
  ```
- Turn on **"Never sleep" / always running**.

> ⚠️ **`render.yaml` is not the escape hatch either.** This guide used to suggest
> Render as an easier alternative to Railway. `render.yaml` is deprecated and
> unmaintained (`render.yaml:1-7`), and its env-name assumptions no longer match
> the app — importing it silently reproduces the `S3_ENDPOINT` config bug that
> stops scorecard PDFs from ever storing. Render and Railway are both superseded;
> the current targets are in the banner at the top of this document.

### 4b. Coding exams use JDoodle (no extra setup)

Coding exams run the candidate's code through **JDoodle**, a hosted API — so
there is **nothing extra to deploy**: no Piston, no VM, no special container.
It works on Railway, Render, or any host.

You only need free JDoodle credentials:

1. Sign up at https://www.jdoodle.com/ → open **Compiler API** → subscribe to the
   **Free** plan (200 runs/day, no card).
2. Copy your **Client ID** and **Client Secret**.
3. In Railway, on the `data-gateway` service, set:
   ```
   EXECUTION_PROVIDER     = jdoodle
   JDOODLE_CLIENT_ID      = <your client id>
   JDOODLE_CLIENT_SECRET  = <your client secret>
   ```

That's it — MCQ exams, coding exams, and interviews all work with just the 5
backend programs.

> Note: the free tier is **200 runs/day**, and each hidden test case is one run.
> Fine for demos; for big exam batches you'd upgrade JDoodle or self-host the
> Piston engine later (it's kept as a fallback via `EXECUTION_PROVIDER=piston`).

---

## 5. Add the settings (environment variables)

Each backend program needs its settings. In Railway, open a service →
**Variables** → add them. The values come from that service's `.env` file.

**Set these the SAME on all 5 programs:**

```
DATABASE_URL      = (from .env — the Neon link)
DATABASE_SSL      = require
REDIS_URL         = (from .env — the Upstash link)
JWT_SECRET        = (the SAME secret on all 5 — copy from one .env)
JWT_ALGORITHM     = HS256
APP_ENV           = production
LOG_LEVEL         = INFO
CORS_ALLOWED_ORIGINS = https://YOUR-SITE.vercel.app   (fill in after Step 8)
```

**Then copy the rest** of each program's own values from its `.env` file
(for example `interview-core` needs the `LIVEKIT_*`, `SARVAM_*`, `TAVUS_*`
keys; `feedback-billing` needs `GEMINI_API_KEY`, etc.).

The fastest way: open the service's `.env`, copy each line, and paste into
Railway's Variables (Railway has a "Raw Editor" where you can paste many lines
at once).

---

## 6. Set up the database tables (run once)

The database starts empty. Run the setup command **one time** on the
`data-gateway` service.

In Railway, open `data-gateway` → **Settings** → run a one-off command (or use
the Railway CLI):

```
alembic -c alembic.ini upgrade head
```

Wait until the logs say `Running upgrade ...`. This creates all the tables.

> Do this **before** users try to log in, or login will fail.

---

## 7. Check the backend is alive

Each web program (not the worker) gets a link like
`https://data-gateway-production-xxxx.up.railway.app`.

Open this in your browser (add `/health/live` at the end):
```
https://<data-gateway link>/health/live
```
You should see: `{"status":"alive"}`. Do this for all 4 web programs.

For the **worker**, there is no link — check its **Logs** in Railway. You should
see a line like `interview-worker — starting worker`.

---

## 8. Deploy the website on Vercel

1. Go to Vercel → **Add New Project** → import your GitHub repo.
2. Settings:
   - **Root Directory:** `web`
   - **Build Command:** `npm run build`
   - **Output Directory:** `dist`
3. Add these **Environment Variables** in Vercel:
   ```
   VITE_API_BASE_URL       = /api/gateway
   VITE_INTERVIEW_API_URL  = /api/interview
   VITE_FEEDBACK_API_URL   = /api/feedback
   VITE_ADMIN_API_URL      = /api/admin
   VITE_LIVEKIT_URL        = (your LiveKit wss:// link)
   VITE_USE_MOCK           = false
   ```
4. Open `web/vercel.json` and make sure the 4 links point to **your** Railway
   programs. Replace each `https://...up.railway.app` with your real Railway
   links (gateway, interview, feedback, admin). Save and push to GitHub.
   > ⚠️ **Step 3 and step 4 no longer apply to the repo at HEAD.** The
   > per-service `/api/*` rewrites they describe were deleted from
   > `web/vercel.json`, and there is nothing in that file to edit. Re-adding the
   > rewrites breaks routing — the file says so at `web/vercel.json:9`.
   >
   > What replaces them **depends on the target**, and the two current targets
   > are opposites — so do not carry a value from one to the other:
   >
   > | Target | `VITE_*_API_URL` | Why |
   > |---|---|---|
   > | **Vercel + a separate backend** (this guide's shape, and the Oracle VM in [`DEPLOY-ORACLE.md`](DEPLOY-ORACLE.md)) | **Full HTTPS URLs** to each backend, e.g. `https://api.example.com` | The SPA and the API are on different origins, so a relative path would resolve to the Vercel host, which serves no API. Cross-origin is also why `AUTH_COOKIE_SAMESITE=none` is set — see the note in Section 1. |
   > | **Hugging Face Space** ← the live one | **Left EMPTY** (the built-in default) | One Caddy serves the SPA and proxies the APIs on the *same* origin, so a relative path is correct and a full URL would break it. `Dockerfile:43-46` already sets these to `""`; `space/Caddyfile:7` records the assumption. There is nothing to configure. |
   >
   > A full HTTPS URL on the Space would send the browser cross-origin to reach
   > a backend that is already same-origin — losing the cookie and the CORS
   > allowance for no gain.
5. Click **Deploy**.

When it finishes, Vercel gives you a link like `https://your-site.vercel.app`.
Go back to Railway (Section 5) and put that link into `CORS_ALLOWED_ORIGINS`
on all 5 programs, then redeploy them.

> **Why the `/api/...` links?** The website talks to the backend through Vercel,
> which quietly forwards the request to Railway. This keeps the login cookie
> working. You do **not** put the Railway links in the `VITE_` settings — only
> in `web/vercel.json`.

---

## 9. Make yourself the admin

After everything is running, make your account the owner/admin. On the
`admin-ops` service in Railway, run this one-off command:

```
python scripts/grant_admin.py support@intants.com
```

(Use your own email if different. First register that email on the website, then
run this command.)

---

## 10. Test the whole thing

Open your Vercel link and go through it like a real user:

1. **Register / log in.**
2. Accept the **privacy consent** popup.
3. Start an **interview** — pick a job, an avatar, a language.
4. The avatar should **appear and talk**, and listen to you.
5. After it ends, open **History** → your **scorecard** with a score + PDF.
6. Log in as admin → open `/admin` → the dashboard should show data.

If all of that works, you are live. 🎉

---

## 11. Safety before sharing the link publicly

This "demo" setup is fine for showing to colleges/companies, but **before**
giving the link to the public, do these:

- **Change all secrets.** The keys in the `.env` files are in the code history,
  so treat them as exposed. Make new ones (new `JWT_SECRET`, new database
  password, new API keys) and update Railway.
- Keep `APP_ENV = production` everywhere.
- For real users, put **Cloudflare** in front (free) to block attacks — login
  has no rate-limit by itself yet.
- **Note:** this demo stores data outside India, so it is **not** allowed for the
  government (APSSDC) bid. That needs the separate "AWS Mumbai" setup later.

---

## 12. If something breaks

| Problem | Likely cause | Fix |
|---|---|---|
| Website loads but login fails | DB tables not set up | Run Section 6 (migrations) |
| "CORS" error in browser | Vercel link not in `CORS_ALLOWED_ORIGINS` | Add it on all 5 services (Section 5) |
| Interview avatar never appears | Worker not running | Check worker logs; set "Never sleep" (Section 4) |
| 404 / "could not load" on a page | Wrong link in `web/vercel.json` | Fix the 4 Railway links (Section 8). **Historical:** `vercel.json` no longer holds backend links — on a current target check the `VITE_*_API_URL` values instead |
| A service won't start | Missing a setting | Compare its Railway Variables with its `.env` |
| Coding exam scores 0 / won't grade | JDoodle creds missing/wrong, or 200/day limit hit | Check `JDOODLE_*` vars (Section 4b); check daily credits |

---

## Quick checklist (of the historical Railway deploy)

> This is the Railway checklist, kept because the *shape* of it — five backend
> processes, shared secrets, one migration, four health checks — is the same on
> every target. Do not work through it as a live to-do list; use the guide for
> your actual target from the banner at the top.

- [ ] Code pushed to GitHub
- [ ] 5 Railway services created (correct Dockerfile path each)
- [ ] Worker set to "Never sleep" + custom start command
- [ ] (For coding exams) JDoodle Client ID + Secret set on data-gateway
- [ ] Variables set on all 5 (shared + each service's own)
- [ ] Database tables created (`alembic upgrade head`)
- [ ] All 4 `/health/live` return "alive"
- [ ] Website deployed on Vercel (root = `web`)
- [ ] `web/vercel.json` points to your Railway links
- [ ] Vercel link added to `CORS_ALLOWED_ORIGINS`
- [ ] Admin account created
- [ ] Full test passed (login → interview → scorecard → admin)
- [ ] Secrets rotated before public sharing
