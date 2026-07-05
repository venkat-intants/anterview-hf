#!/usr/bin/env bash
# ===========================================================================
# One-shot setup for deploying the full backend on an Oracle Cloud VM
# (Ubuntu 22.04). Run from the repo root, as a sudo user:
#
#   sudo \
#     PUBLIC_WEB_ORIGIN=https://your-app.vercel.app \
#     PUBLIC_API_DOMAIN=api.yourdomain.com \
#     ./scripts/oracle-setup.sh
#
# REQUIRED variables:
#   PUBLIC_WEB_ORIGIN  — your Vercel frontend URL, e.g. https://your-app.vercel.app
#   PUBLIC_API_DOMAIN  — a domain (or subdomain) whose DNS A-record points to
#                        this VM's public IP, e.g. api.yourdomain.com.
#                        Caddy uses this domain to obtain a Let's Encrypt TLS
#                        certificate automatically. The domain MUST resolve to
#                        this VM BEFORE you run this script, otherwise Caddy
#                        cannot complete the ACME HTTP-01 challenge and will
#                        refuse to start with a TLS error.
#
# It will:
#   1. install Docker + compose plugin
#   2. open the host firewall for ports 80 + 443 ONLY (Caddy TLS proxy)
#   3. write deploy.env
#   4. build + start all 6 backend containers
#   5. run the database migrations (once)
#
# Coding exams run via JDoodle (hosted API) — NO Piston, NO VM extras. Make sure
# services/data_gateway/.env has EXECUTION_PROVIDER=jdoodle + JDOODLE_CLIENT_ID +
# JDOODLE_CLIENT_SECRET (free creds at https://www.jdoodle.com/).
#
# NOTE: open ONLY 80 + 443 in the OCI Console
#       (Networking > VCN > Security List > Ingress). Do NOT open 8001-8004.
# ===========================================================================
set -euo pipefail

# Re-run with sudo if not root.
if [ "$(id -u)" -ne 0 ]; then exec sudo -E "$0" "$@"; fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PUBLIC_WEB_ORIGIN="${PUBLIC_WEB_ORIGIN:-}"
PUBLIC_API_DOMAIN="${PUBLIC_API_DOMAIN:-}"

# --------------------------------------------------------------------------
# Validate required inputs BEFORE touching the system (fail fast).
# --------------------------------------------------------------------------
if [ -z "$PUBLIC_WEB_ORIGIN" ] && [ ! -f deploy.env ]; then
  echo ""
  echo "ERROR: PUBLIC_WEB_ORIGIN is not set and deploy.env does not exist."
  echo ""
  echo "Re-run with both required variables:"
  echo "  sudo PUBLIC_WEB_ORIGIN=https://your-app.vercel.app \\"
  echo "       PUBLIC_API_DOMAIN=api.yourdomain.com \\"
  echo "       $0"
  echo ""
  exit 1
fi

if [ -z "$PUBLIC_API_DOMAIN" ] && [ ! -f deploy.env ]; then
  echo ""
  echo "ERROR: PUBLIC_API_DOMAIN is not set and deploy.env does not exist."
  echo ""
  echo "Caddy needs a real domain name (not an IP address) to obtain a"
  echo "TLS certificate from Let's Encrypt via the ACME HTTP-01 challenge."
  echo ""
  echo "What you must do BEFORE re-running this script:"
  echo "  1. Pick (or create) a domain or subdomain, e.g. api.yourdomain.com"
  echo "  2. Add a DNS A-record:  api.yourdomain.com  ->  $(curl -fsS https://api.ipify.org 2>/dev/null || echo '<this-vm-public-ip>')"
  echo "  3. Wait for the DNS record to propagate (usually < 5 minutes)."
  echo "     Verify with:  nslookup api.yourdomain.com"
  echo "  4. Re-run:"
  echo "       sudo PUBLIC_WEB_ORIGIN=https://your-app.vercel.app \\"
  echo "            PUBLIC_API_DOMAIN=api.yourdomain.com \\"
  echo "            $0"
  echo ""
  echo "Without a valid domain Caddy will fail the ACME challenge and refuse"
  echo "to serve HTTPS. The stack cannot run without TLS."
  echo ""
  exit 1
fi

echo "==> Repo: $REPO_ROOT   Arch: $(uname -m)"

# --- 1. Docker -------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker ..."
  curl -fsSL https://get.docker.com | sh
fi
# Ensure the daemon starts on boot so `restart: unless-stopped` containers come
# back after a VM reboot/power-cycle (get.docker.com usually does this, but make
# it explicit + idempotent).
systemctl enable --now docker >/dev/null 2>&1 || true

# --- 1b. Swap — Oracle ARM Free Tier ships with NONE. A small swap file gives
# the host a page-out cushion so a memory spike degrades instead of hard-OOM-
# killing a container (which would drop live interviews with no drain).
if ! swapon --show 2>/dev/null | grep -q '/swapfile'; then
  if [ ! -f /swapfile ]; then
    echo "==> Creating 4G swap file ..."
    fallocate -l 4G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=4096
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile || true
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Installing iptables-persistent ..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y iptables-persistent

# --- 2. Host firewall (Oracle Ubuntu blocks everything but SSH by default) --
# ONLY Caddy (the TLS reverse proxy) is public: ports 80 + 443. The four app
# services (8001-8004) are INTERNAL to the Docker network and must NEVER be
# opened to the internet — doing so serves candidate PII + JWTs in CLEARTEXT,
# bypassing TLS and Caddy. (Older versions of this script opened 8001-8004; that
# was a security bug — do not reinstate it.)
echo "==> Opening host firewall for 80 + 443 (Caddy TLS proxy only) ..."
for p in 80 443; do
  iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null \
    || iptables -I INPUT -p tcp --dport "$p" -j ACCEPT
done
netfilter-persistent save || true
echo "    (Also open ONLY 80 + 443 in the OCI Console Security List — NOT 8001-8004.)"

# --- 3. deploy.env ---------------------------------------------------------
# Both variables are required; we already validated them above.
# If deploy.env already exists (re-run scenario) we honour the existing file
# but warn if either key is missing from it.
if [ ! -f deploy.env ]; then
  {
    echo "PUBLIC_WEB_ORIGIN=${PUBLIC_WEB_ORIGIN}"
    echo "PUBLIC_API_DOMAIN=${PUBLIC_API_DOMAIN}"
  } > deploy.env
  echo "==> Wrote deploy.env"
else
  # Patch in any variable that was passed on the CLI but is missing from the file.
  if [ -n "$PUBLIC_WEB_ORIGIN" ] && ! grep -q "^PUBLIC_WEB_ORIGIN=" deploy.env; then
    echo "PUBLIC_WEB_ORIGIN=${PUBLIC_WEB_ORIGIN}" >> deploy.env
    echo "==> Appended PUBLIC_WEB_ORIGIN to existing deploy.env"
  fi
  if [ -n "$PUBLIC_API_DOMAIN" ] && ! grep -q "^PUBLIC_API_DOMAIN=" deploy.env; then
    echo "PUBLIC_API_DOMAIN=${PUBLIC_API_DOMAIN}" >> deploy.env
    echo "==> Appended PUBLIC_API_DOMAIN to existing deploy.env"
  fi
  if ! grep -q "^PUBLIC_API_DOMAIN=" deploy.env; then
    echo ""
    echo "ERROR: deploy.env exists but is missing PUBLIC_API_DOMAIN."
    echo "Add it manually:  echo 'PUBLIC_API_DOMAIN=api.yourdomain.com' >> deploy.env"
    echo "Then re-run this script."
    echo ""
    exit 1
  fi
fi
echo "==> Using deploy.env:"; sed 's/^/    /' deploy.env

COMPOSE="docker compose --env-file deploy.env -f docker-compose.prod.yml"

# --- 3b. Secrets hygiene ---------------------------------------------------
# The per-service .env files (scp'd onto the VM) hold DB/JWT/API secrets. scp
# commonly lands them world-readable (0644). Lock them to owner-only so no other
# local user or process can read plaintext credentials off disk. Also fail fast
# if a required .env is missing (compose would otherwise start with empty config).
echo "==> Securing service .env files (chmod 600) ..."
_missing_env=0
for svc in data_gateway interview_core feedback_billing admin_ops; do
  if [ -f "services/$svc/.env" ]; then
    chmod 600 "services/$svc/.env"
  else
    echo "    MISSING: services/$svc/.env — copy it onto the VM before deploying."
    _missing_env=1
  fi
done
if [ "$_missing_env" = 1 ]; then
  echo "ERROR: one or more service .env files are missing (see above)."
  exit 1
fi

# --- 4. Build + start ------------------------------------------------------
echo "==> Building images (first build takes several minutes) ..."
$COMPOSE build
echo "==> Starting all containers ..."
$COMPOSE up -d

# --- 5. Migrations (wait for data_gateway to be healthy, then upgrade once) --
# We wait on the internal port (Docker network is accessible from the host
# because compose publishes no ports for data_gateway — but the container's
# port IS reachable from the host via the Docker bridge). Using the internal
# address here is intentional: at migration time Caddy may still be obtaining
# its TLS cert, so we bypass it.
echo "==> Waiting for data_gateway to become healthy ..."
for i in $(seq 1 40); do
  if $COMPOSE exec -T data_gateway python -c \
       "import urllib.request; urllib.request.urlopen('http://localhost:8002/health/live', timeout=5)" \
       >/dev/null 2>&1; then
    break
  fi
  sleep 3
  [ "$i" = 40 ] && { echo "data_gateway did not come up — check: $COMPOSE logs data_gateway"; exit 1; }
done
echo "==> Running database migrations ..."
$COMPOSE exec -T data_gateway alembic -c alembic.ini upgrade head

# --- Done ------------------------------------------------------------------
# Load PUBLIC_API_DOMAIN from deploy.env if it was not set in the environment
# (re-run scenario where the caller did not export it but it lives in the file).
if [ -z "${PUBLIC_API_DOMAIN:-}" ]; then
  PUBLIC_API_DOMAIN="$(grep '^PUBLIC_API_DOMAIN=' deploy.env | cut -d= -f2-)"
fi

cat <<EOF

============================================================
 DONE. The 6 backend containers are running behind Caddy TLS.

 Internal health checks (service is alive inside Docker):
   $COMPOSE exec -T data_gateway    python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health/live', timeout=5)"
   $COMPOSE exec -T interview_core  python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health/live', timeout=5)"
   $COMPOSE exec -T feedback_billing python -c "import urllib.request; urllib.request.urlopen('http://localhost:8003/health/live', timeout=5)"
   $COMPOSE exec -T admin_ops       python -c "import urllib.request; urllib.request.urlopen('http://localhost:8004/health/live', timeout=5)"

 Public health check (goes through Caddy + TLS):
   curl https://${PUBLIC_API_DOMAIN}/health
   # Should return {"status":"alive"} once the TLS cert is issued (< 60 s).

 NEXT:
 1. OCI Console > VCN > Security List: ensure ONLY ports 80 + 443 are open.
    Do NOT open 8001-8004 — those are internal service ports; opening them
    exposes candidate PII and JWTs in cleartext, bypassing TLS.
 2. In the Vercel project settings, set ALL FOUR frontend API base URLs to your
    Caddy HTTPS domain (the app calls these directly; it does NOT use
    vercel.json rewrites). Caddy fans each bare path out to the right service:
      VITE_API_BASE_URL=https://${PUBLIC_API_DOMAIN}
      VITE_INTERVIEW_API_URL=https://${PUBLIC_API_DOMAIN}
      VITE_FEEDBACK_API_URL=https://${PUBLIC_API_DOMAIN}
      VITE_ADMIN_API_URL=https://${PUBLIC_API_DOMAIN}
      VITE_USE_MOCK=false
    (Use your Caddy-issued HTTPS domain — not http://VM-IP:800x.)
    Then redeploy the Vercel frontend.
 3. Make yourself admin:
      $COMPOSE exec -T admin_ops python scripts/grant_admin.py YOUR_EMAIL

 Coding exams use JDoodle — confirm services/data_gateway/.env has
 EXECUTION_PROVIDER=jdoodle + JDOODLE_CLIENT_ID + JDOODLE_CLIENT_SECRET.

 Manage:  $COMPOSE ps | logs -f <svc> | down
============================================================
EOF
