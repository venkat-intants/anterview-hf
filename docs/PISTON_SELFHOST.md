# Self-hosting Piston for the coding round

The coding round runs candidate code in a sandbox via **Piston**. The free
**public** Piston API (`emkc.org`) became **whitelist-only on 2026‑02‑15**, so it
now returns `HTTP 401 "Public Piston API is now whitelist only…"` for everyone.

The fix is to run your **own** Piston — free, no card, no whitelist, and it's also
the path to **India data residency** for production (self-host in Mumbai). The
execution client is swappable, so this is a config change, not a code change.

---

## One-time setup (local dev, Windows)

### 1. Install Docker
Install **Docker Desktop** → https://www.docker.com/products/docker-desktop/ —
then **launch it** and wait until it says "Engine running".

### 2. Start Piston + install the languages
From the repo root:

```powershell
.\scripts\piston-up.ps1
```

This runs the official Piston container on `127.0.0.1:2000` and installs every
language the coding round supports (Python, JavaScript, TypeScript, Java, C++, C,
Go, C#, Ruby, Rust). The first run downloads each runtime, so it takes a few
minutes.

Works on Docker Desktop (WSL2 backend) out of the box.

---

## Sandbox privilege posture

This container **executes code written by candidates**, so it gets the narrowest
privilege set that still runs. It used to run `--privileged`, which grants every
capability, all host devices, an unmasked `/proc` and an unconfined
seccomp/AppArmor profile — removing the isolation that is the entire point of a
sandbox. Changed 2026-08-06.

| Control | Before | Now |
|---|---|---|
| Capabilities | all (`--privileged`) | `--cap-drop=ALL` plus 11 named caps |
| Host devices | all | none |
| `/proc` masking | unmasked | default masks apply |
| Candidate-code network | `--dns 8.8.8.8` (deliberate egress) | none — per-job netns with no interfaces |
| API port | `0.0.0.0:2000` | `127.0.0.1:2000` |
| Process cap | unbounded | `--pids-limit 512` |

The capability list was derived empirically — each one was removed and the
failure observed — not guessed:

`SYS_ADMIN` (isolate's mount/namespace work) · `SYS_CHROOT` · `SYS_RESOURCE`
(rlimits) · `NET_ADMIN` · `SETUID`/`SETGID` (drop to the piston user) ·
`CHOWN`/`DAC_OVERRIDE`/`FOWNER`/`MKNOD` (per-job box filesystem) · `KILL`

`NET_ADMIN` reads backwards but is correct: isolate uses it to bring up `lo`
*inside the job's otherwise-empty network namespace*. It is the capability that
lets the sandbox finish locking itself down; without it every run dies with
`SIOCSIFFLAGS on 'lo' failed`.

### What this does *not* buy you

**`CAP_SYS_ADMIN` is close to root.** Combined with `apparmor=unconfined` (isolate
needs mount operations the default profile denies), a container escape is not off
the table. This change removes the *easy* paths — host device access, unmasked
`/proc`, the full capability set — so treat it as **materially harder to escape,
not isolated**. Do not run the self-hosted runner on a host that holds production
credentials.

**Hosted providers are outside this boundary entirely.** The public `emkc.org`
Piston fallback and the hosted JDoodle provider execute candidate code on
third-party infrastructure we do not control and cannot isolate. That is an
accepted risk, recorded here so it is a decision rather than an oversight — no
container flag changes it.

### Verified behaviour

Checked against the running container, not assumed:

| Check | Result |
|---|---|
| `docker inspect … .HostConfig.Privileged` | `false`, `CapDrop=[ALL]` |
| Ordinary program runs | `print(sum(range(10)))` → `45`, exit 0 |
| Compiled language runs | C++ compile + run → `42` |
| Candidate code opening a socket | `[Errno 101] Network is unreachable` |
| Container's own egress (needed to install runtimes) | still works |
| Infinite loop under `run_timeout` | `signal: SIGKILL`, `status: TO` |

That last row matters beyond the sandbox: `piston_client._parse` maps
`SIGKILL`/`SIGXCPU` to `timed_out=True`, so a job the stricter sandbox kills is
still graded as a **timeout fail** rather than surfacing as a crash. The tighter
container did not change how a killed run is reported.

### Cgroups (why the script has a cleanup step)

`--privileged` implicitly mounted the container's own cgroup subtree read-write.
Without it Docker mounts `/sys/fs/cgroup` read-only and Piston's entrypoint dies
on `mkdir: cannot create directory 'isolate/': Read-only file system`, so the
script bind-mounts the cgroup2 root `rw`. That bind mount overrides `--cgroupns`,
so the `isolate` cgroup lands in the **host** hierarchy and outlives the
container — a second run would fail with `File exists`. Step 0 of the script
removes the stale tree with a minimal helper container (`--cap-drop=ALL`, one
capability) so re-running stays safe.

### 3. Point the app at it (already wired)
`services/data_gateway/.env` is already set to:

```
EXECUTION_PROVIDER=piston
PISTON_API_URL=http://localhost:2000/api/v2
```

### 4. Restart data_gateway
So it reads the new `PISTON_API_URL`:

```powershell
# stop the old data_gateway window, then from the repo root:
cd services\data_gateway
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8002
```

### 5. Verify
- `curl http://localhost:2000/api/v2/runtimes` lists installed languages.
- In the app, open a coding exam → **Run samples** → you should now get
  pass/fail results instead of the 401.

---

## Managing the container

```powershell
docker ps                    # is piston_api running?
docker logs piston_api       # logs
docker stop piston_api       # stop (start again with scripts\piston-up.ps1)
docker rm -f piston_api      # remove entirely
```

Re-running `scripts\piston-up.ps1` is safe — it starts the existing container and
skips already-installed languages.

---

## Production (Tier-2, India residency)

Run the same Piston image as its **own service** on **AWS Mumbai** (never inside
`data_gateway`), then set `PISTON_API_URL` to that internal URL. Untrusted code
then executes in-region, in an isolated service you scale independently — and the
≤₹12/session cost stays on your own compute.

Carry the local privilege posture across, and add what a single host cannot give
you: a dedicated node pool with no IAM role beyond image pull, a NetworkPolicy
that denies all egress from the execution pods, and gVisor or Kata as the runtime
so `CAP_SYS_ADMIN` stops being the last line of defence. The caveat above —
"materially harder to escape, not isolated" — is a statement about the local
dev setup; production should not settle for it.

> **Known v1 limitation:** grading is **synchronous** (it runs every hidden test
> in one request), so a coding exam should keep test counts modest
> (`CODE_MAX_TEST_CASES`, default 20). Moving grading to a background task is the
> Tier-2 improvement (flagged to `cto-architect`).
