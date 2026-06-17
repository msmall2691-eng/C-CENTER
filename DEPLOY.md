# Deploying Command Center — Vercel (frontend) + Railway (backend)

A step-by-step guide to host the UI on **Vercel** and the agent backend on
**Railway**. Hand this to a Claude coworker (or follow it yourself).

Repo: `msmall2691-eng/c-center`

---

## ⚠️ Read this first — what the agents can actually touch

This decides whether Railway is right for you.

The backend runs the agents with **real tools scoped to `WORKSPACE_DIR`**. On
Railway, that directory lives **inside a cloud container — not on your
computer.** So:

- **Field & School agents (advisors)** — work perfectly on Railway. They only
  read/search and draft text; they don't need your local files. ✅
- **Build Crew agents** — on Railway they edit files **in the container**, not
  on your Mac. They can only work on code that's *in* the container (e.g. a repo
  you clone in), and their changes live there until they `git push`. ⚠️
- **If you want agents that edit your *local* BrightBase code**, Railway is the
  wrong tool — use the **Tailscale** setup in `README.md` instead, which keeps
  the backend on your own machine next to your code.

**Rule of thumb:** Railway = an always-on cloud command center for advisors +
agents that work on cloud/git repos. Tailscale = agents that touch your laptop's
files. You can run both.

---

## Part 1 — Backend on Railway

Railway builds the backend from `backend/Dockerfile` (already in the repo — it
installs Python **and** Node, which the Agent SDK requires).

1. Go to **[railway.app](https://railway.app)** → sign in with GitHub.
2. **New Project → Deploy from GitHub repo** → pick `msmall2691-eng/c-center`.
3. Open the service → **Settings**:
   - **Root Directory:** `backend`  (so it finds the Dockerfile and code)
   - Railway will auto-detect the Dockerfile. No start command needed — it's in
     the Dockerfile (`uvicorn main:app --host 0.0.0.0 --port $PORT`).
4. **Variables** tab — add:
   | Variable | Value | Required? |
   |---|---|---|
   | `ANTHROPIC_API_KEY` | your key from console.anthropic.com | **Yes** |
   | `AGENT_MODEL` | e.g. `haiku` to force every agent cheap (omit to use per-agent defaults) | No |
   | `WORKSPACE_DIR` | a path in the container for Build agents, e.g. `/app/workspace` | No |
   | `HISTORY_DB` | `/data/history.db` (see step 5 for persistence) | No |
5. **Persist conversation memory across redeploys** (optional but recommended):
   - Service → **Volumes → New Volume**, mount path `/data`.
   - Set `HISTORY_DB=/data/history.db` (from step 4). Without a volume, saved
     chats reset on every redeploy.
6. **Networking → Generate Domain.** You'll get a URL like
   `https://command-center-production.up.railway.app`. **Copy it** — the
   frontend needs it. (No port number; Railway serves HTTPS on 443.)
7. Wait for the deploy to go green, then test:
   `https://<your-railway-domain>/api/health` should return
   `{"ok": true, "agents": 15, ...}`.

> If the build fails, it's almost always the runtime — confirm the service is
> building from `backend/Dockerfile` (Root Directory = `backend`), not Nixpacks.

---

## Part 2 — Frontend on Vercel

1. Go to **[vercel.com](https://vercel.com)** → sign in with GitHub.
2. **Add New → Project** → import `msmall2691-eng/c-center`.
3. Configure:
   - **Root Directory:** `frontend`
   - **Framework Preset:** Vite (auto-detected). Build `npm run build`, output
     `dist` — leave as defaults.
4. **Environment Variables** — add **one**, so the UI knows where the backend is:
   | Variable | Value |
   |---|---|
   | `VITE_BACKEND` | your Railway domain **without** `https://`, e.g. `command-center-production.up.railway.app` |
5. **Deploy.** You'll get a URL like `https://c-center.vercel.app`.

That's it — the frontend reads `VITE_BACKEND` and automatically uses secure
`https://` + `wss://` to reach Railway. (If you ever skip the env var, you can
instead open `https://<vercel-url>/?backend=<railway-domain>` once; it's
remembered in the browser.)

---

## Part 3 — Use it

1. Open your Vercel URL (on any device — add it to your phone's home screen).
2. The dot top-right turns **green** when it reaches the Railway backend.
3. Pick an **advisor** agent (Field/School) and send a message — works anywhere.
4. For **Build** agents, remember they act on the Railway container's files.

### Cost
Every message bills your Anthropic **API key** per token (separate from any
Claude subscription). Models are per-agent (2 Opus, 5 Sonnet, 8 Haiku — see
`backend/roster.py`); set `AGENT_MODEL=haiku` on Railway to force everything to
the cheapest model. Watch spend at **console.anthropic.com → Usage** and set a
limit there.

---

## Quick reference

| Piece | Host | Key setting |
|---|---|---|
| Frontend (UI) | Vercel | Root Dir `frontend`, env `VITE_BACKEND=<railway-domain>` |
| Backend (agents) | Railway | Root Dir `backend`, env `ANTHROPIC_API_KEY`, Dockerfile auto-built |
| Memory | Railway Volume | mount `/data`, env `HISTORY_DB=/data/history.db` |

**Two architectures, pick per need:**
- **Vercel + Railway** (this guide) — always-on cloud, advisors + cloud/git work.
- **Vercel/Tailscale + your machine** (`README.md`) — agents edit your local code.
