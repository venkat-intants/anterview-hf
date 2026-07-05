# Deploy on Oracle Cloud (Full Backend) — Simple Guide

This puts the **whole backend** (6 Docker containers) on **one free Oracle Cloud
VM**. The **website** goes on **Vercel** (separate, free). The database stays on
**Neon**. Coding exams run via **JDoodle** (a hosted API — no extra container).
Coding exams, MCQ exams, and interviews all work.

You will use 2 files that are already in this repo:
- `docker-compose.prod.yml` — defines the 6 containers (Caddy + 4 FastAPI services + interview worker)
- `scripts/oracle-setup.sh` — sets everything up with one command

---

## Before you start

- An **Oracle Cloud** account (free): https://www.oracle.com/cloud/free/
- Your code on **GitHub** (the VM will download it).
- Your `services/*/.env` files ready (they hold your keys — they are NOT on GitHub).
- Your **Vercel** website link, e.g. `https://your-app.vercel.app`.
- A **domain name** (or subdomain) whose DNS A-record points to this VM's public IP,
  e.g. `api.yourdomain.com`. Caddy uses this domain to get a free TLS certificate from
  Let's Encrypt. **Without a real domain the stack cannot serve HTTPS and will not start.**
  Point the A-record before running the setup script and verify with `nslookup api.yourdomain.com`.
- **JDoodle credentials** (free, for coding exams): `services/data_gateway/.env` must have
  `EXECUTION_PROVIDER=jdoodle`, `JDOODLE_CLIENT_ID=...`, `JDOODLE_CLIENT_SECRET=...`
  (get them free at https://www.jdoodle.com/).

> **Good news — no ARM headaches.** Because coding exams use JDoodle (a hosted
> API call), there is **no Piston and no privileged container** to run. All 6
> backend containers are plain Python + Caddy and run perfectly on Oracle's free
> **ARM** VM with no special setup.

---

## Step 1 — Make the VM

1. Log in to Oracle Cloud → menu → **Compute** → **Instances** → **Create instance**.
2. **Image and shape:**
   - Image: **Ubuntu 22.04**
   - Shape: **Ampere (Arm)** → `VM.Standard.A1.Flex` → set **4 OCPU** and **24 GB RAM**
     (all free). If it says "out of capacity", try another Availability Domain or
     region, or come back later — this is common with the free ARM tier.
3. **SSH keys:** choose **Save private key** (download it — you need it to log in).
4. Click **Create**. When it's running, copy its **Public IP address**.

## Step 2 — Open the ports (do this in the Oracle website)

Open **only 80 and 443** — the Caddy TLS reverse proxy is the single public
entrypoint. **Do NOT open 8001–8004**: those are the internal service ports and
exposing them serves candidate PII + JWTs in **cleartext**, bypassing TLS.

1. Open your instance → click its **Virtual Cloud Network (VCN)** → **Security Lists**
   → **Default Security List**.
2. **Add Ingress Rules** → one rule each for **80** and **443**:
   - Source CIDR: `0.0.0.0/0`
   - IP Protocol: **TCP**
   - Destination Port Range: `80` (then a second rule for `443`)
3. Save.

(The setup script opens the same two ports *inside* the VM. You need **both**.)
Caddy needs a domain (`PUBLIC_API_DOMAIN`) resolving to this VM's IP for HTTPS,
and your Vercel rewrites must point at `https://<that-domain>` — see Step 7.

## Step 3 — Connect to the VM

On your computer, in a terminal:

```bash
chmod 600 your-key.key
ssh -i your-key.key ubuntu@<YOUR-VM-IP>
```

You are now inside the VM.

## Step 4 — Get the code and your .env files onto the VM

Download the code:

```bash
sudo apt-get update -y && sudo apt-get install -y git
git clone https://github.com/<your-username>/anterviewer.git
cd anterviewer
```

Now copy your **`.env` files** onto the VM (they are not in GitHub). From **your
own computer** (open a second terminal), run this once per service:

```bash
scp -i your-key.key services/data_gateway/.env      ubuntu@<VM-IP>:~/anterviewer/services/data_gateway/.env
scp -i your-key.key services/interview_core/.env    ubuntu@<VM-IP>:~/anterviewer/services/interview_core/.env
scp -i your-key.key services/feedback_billing/.env  ubuntu@<VM-IP>:~/anterviewer/services/feedback_billing/.env
scp -i your-key.key services/admin_ops/.env         ubuntu@<VM-IP>:~/anterviewer/services/admin_ops/.env
```

(Make sure `services/data_gateway/.env` has your JDoodle credentials — see "Before you start".)

## Step 5 — Run the one-command setup (back inside the VM)

```bash
cd ~/anterviewer
sudo \
  PUBLIC_WEB_ORIGIN=https://your-app.vercel.app \
  PUBLIC_API_DOMAIN=api.yourdomain.com \
  ./scripts/oracle-setup.sh
```

Both variables are **required**:
- `PUBLIC_WEB_ORIGIN` — your Vercel frontend URL (used for CORS, cookie domain, email links).
- `PUBLIC_API_DOMAIN` — the domain or subdomain whose DNS A-record points to this VM.
  Caddy uses it to automatically obtain a Let's Encrypt TLS certificate via ACME.
  The script will exit with a clear error if this is missing or if `deploy.env` already
  exists but does not contain it.

The script installs Docker, opens the firewall (ports 80 + 443 only), builds the 6 images
(first build takes several minutes), starts all containers, and runs the database migrations.

## Step 6 — Check it's alive

First check that all containers are healthy (give them ~60 s to start):

```bash
docker compose --env-file deploy.env -f docker-compose.prod.yml ps
```

All services should show `healthy`. Then verify the public TLS endpoint:

```bash
curl https://api.yourdomain.com/health     # should print {"status":"alive"}
```

This goes through Caddy and TLS — exactly what the browser (and Vercel) will use.
If you want to check internal service health directly (bypassing Caddy), use `exec`:

```bash
C="docker compose --env-file deploy.env -f docker-compose.prod.yml"
$C exec -T data_gateway     python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health/live', timeout=5)"
$C exec -T interview_core   python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health/live', timeout=5)"
$C exec -T feedback_billing python -c "import urllib.request; urllib.request.urlopen('http://localhost:8003/health/live', timeout=5)"
$C exec -T admin_ops        python -c "import urllib.request; urllib.request.urlopen('http://localhost:8004/health/live', timeout=5)"
```

Do NOT try to `curl http://VM-IP:8002` from outside — those ports are closed by design.
To watch logs: `docker compose --env-file deploy.env -f docker-compose.prod.yml logs -f`.

## Step 7 — Connect the website (Vercel)

The frontend calls your Caddy HTTPS domain **directly** via four build-time env
vars — it does NOT use `vercel.json` rewrites (those are legacy and ignored).
Caddy fans each bare API path out to the correct backend service, so all four
URLs point at the **same** domain:

1. In the Vercel project → **Settings → Environment Variables**, set (Production):
   ```
   VITE_API_BASE_URL=https://api.yourdomain.com
   VITE_INTERVIEW_API_URL=https://api.yourdomain.com
   VITE_FEEDBACK_API_URL=https://api.yourdomain.com
   VITE_ADMIN_API_URL=https://api.yourdomain.com
   VITE_USE_MOCK=false
   ```
   (Use your real Caddy domain — not `http://VM-IP:800x`; the raw service ports
   are closed.) `VITE_USE_MOCK=false` is required — the app defaults to mock
   data if it is unset.
2. **Redeploy** the Vercel frontend so the new env vars are baked into the build.

The browser talks to Vercel (static assets, HTTPS) and to `api.yourdomain.com`
(the API, HTTPS via Caddy). All TLS is end-to-end. The VM's raw service ports
(8001-8004) remain closed to the internet.

## Step 8 — Make yourself admin, then test

```bash
docker compose --env-file deploy.env -f docker-compose.prod.yml exec -T admin_ops \
  python scripts/grant_admin.py YOUR_EMAIL
```
(Register that email on the website first.) Then open your Vercel link and test:
login → MCQ exam → **coding exam (uses JDoodle)** → interview → scorecard → admin.

---

## Everyday commands (run inside `~/anterviewer`)

```bash
C="docker compose --env-file deploy.env -f docker-compose.prod.yml"
$C ps                 # status of all containers
$C logs -f data_gateway   # watch one service
$C restart data_gateway   # restart one
$C down               # stop everything
$C up -d --build      # rebuild + start (after a git pull)
```

## If something breaks

| Problem | Cause | Fix |
|---|---|---|
| `curl https://api.yourdomain.com/health` returns TLS error / connection refused | Caddy not yet up or domain not yet resolving to this VM | Check DNS: `nslookup api.yourdomain.com`; check Caddy logs: `$C logs caddy` |
| Caddy log shows "no DNS records" or "ACME challenge failed" | DNS A-record for `PUBLIC_API_DOMAIN` not set, or not yet propagated | Add/wait for the A-record to point to this VM's public IP, then `$C restart caddy` |
| `curl https://api.yourdomain.com/health` returns 502 | Upstream service (data_gateway) is down | `$C ps` to see unhealthy containers; `$C logs data_gateway` |
| Setup script exits with "PUBLIC_API_DOMAIN is not set" | Variable missing on CLI and not in `deploy.env` | Add the DNS A-record first, then re-run with both variables |
| Vercel rewrites return 404 or connection refused | Rewrites still point at `http://VM-IP:800x` | Update `vercel.json` to use `https://api.yourdomain.com` and redeploy |
| Health works inside VM but not from browser | Rewrites using old HTTP/IP URLs | Same fix as above |
| Coding exam fails / shows 0 | JDoodle creds missing/wrong, or 200/day limit hit | Check `JDOODLE_*` in `data_gateway/.env`; check daily credits |
| Exam/interview invite emails link to localhost | `PUBLIC_WEB_ORIGIN` wrong in `deploy.env` | Fix `deploy.env`, then `$C up -d` again |
| A container keeps restarting | Missing setting in its `.env` | `$C logs <svc>` to see what's missing |
| `interview_worker` shows `unhealthy` | Worker not writing heartbeat — crashed or stalled | `$C logs interview_worker`; check LiveKit env vars in `services/interview_core/.env` |
| "CORS" error in browser | Vercel link not in allowed origins | `PUBLIC_WEB_ORIGIN` in `deploy.env` must match your exact Vercel URL; `$C up -d` after fixing |

## Before sharing publicly

- **Rotate all secrets** in the `.env` files (they're in the code history).
- Put **Cloudflare** in front of the VM (login has no rate-limit yet).
- This setup stores data outside India — fine for demos, not for the government bid.

## Data residency and DPDP disclosure

This deployment is **NOT India-resident**. Candidate data flows to sub-processors
in **Singapore** (Neon database), **United States** (Groq/Gemini LLM, Cloudflare R2
storage, Tavus/Simli avatar, LiveKit), and global edge (Upstash). This is the
"Tier-1 demo" stack — the same codebase is env-swappable to Tier-2 (AWS Mumbai,
India-resident) without code changes.

**What this means for consent:** the in-app DPDP consent modal shows candidates
a cross-border disclosure notice before they consent. Do NOT remove or weaken
this notice. Do NOT add any wording that claims India residency until Tier-2
(AWS Mumbai) is confirmed live.

Full sub-processor details and the India-residency migration plan: `docs/DATA-FLOW.md`
