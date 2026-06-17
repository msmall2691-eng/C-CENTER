# Command Center (local)

Your own agent command center — your UI, running on your machine, driving real
local agents on the **Claude Agent SDK**. Build-crew agents can read and search
your code freely, and **write files, edit files, and run shell commands (git
included) — but every one of those waits for your tap.** Field and school agents
are read-only advisors.

```
command-center-local/
├── backend/      FastAPI + claude-agent-sdk (the engine)
│   ├── main.py       WebSocket + approval gate
│   ├── roster.py     your agents (edit me)
│   ├── requirements.txt
│   └── .env.example
└── frontend/     Vite + React (the command center)
    └── src/App.jsx
```

## Prerequisites

- **Python 3.10+**
- **Node.js 18+** (the Agent SDK bundles the Claude Code runtime, which runs on Node)
- An **Anthropic API key** from https://console.anthropic.com

## 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and set two things:

- `ANTHROPIC_API_KEY` — your key
- `WORKSPACE_DIR` — the folder build agents may work in (point it at BrightBase)

Then run it:

```bash
uvicorn main:app --reload --port 8000
```

## 2. Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The dot in the top right turns green when it's
talking to the backend.

## Quick start (both servers, one command)

Once the one-time setup above is done (deps installed, `backend/.env` filled in),
you don't need two terminals. From the project root:

```bash
./start.sh
```

It launches the backend (`:8000`) and frontend (`:5173`) together, both bound to
`0.0.0.0` so other devices can reach them, prints your phone URL if Tailscale is
running, and stops both cleanly when you press **Ctrl+C**.

## Run it across your network (drive agents from another device)

The setup above is single-machine. To run the agents on one computer (a spare
laptop, a Mac mini, a home server) and command them from your phone or a second
laptop — the way you watch them work on one screen and chat from another — bind
both servers to all interfaces instead of just localhost:

```bash
# Backend — on the server machine:
uvicorn main:app --host 0.0.0.0 --port 8000

# Frontend — on the server machine:
npm run dev -- --host
```

Find the server's LAN address (`ipconfig getifaddr en0` on macOS, `hostname -I`
on Linux), then from any device on the same Wi-Fi open:

```
http://<server-ip>:5173
```

The UI automatically targets the backend at that **same host** on port 8000 — no
rebuild, no editing code. The agents run on the server machine (you'll see their
work in that terminal); you drive and approve from whatever device you opened.

**From outside your home network**, put the server on a private mesh like
[Tailscale](https://tailscale.com) (every device gets a stable IP) and use that
IP above, or expose port 8000 with a tunnel (`cloudflared` / `ngrok`) and point
the UI at it once with `?backend=`:

```
http://<frontend-url>/?backend=your-tunnel-host.example.com
```

That address is remembered for next time. HTTPS pages automatically use secure
`wss://` sockets. Because anyone who can reach the backend can drive your
build-crew agents, only expose it over a trusted network (Tailscale) or behind
auth — not the raw public internet.

## How the approval gate works

1. You brief an agent in plain English ("add a `PATCH /api/clients/:id` handler").
2. It reads whatever files it needs (no prompts — reads are safe).
3. The moment it wants to **write a file, edit one, or run a command**, the work
   pauses and an **Approval needed** card shows you the exact file diff or the
   exact shell command.
4. You **Approve & run** or **Decline**. Nothing touches your machine until you do.

This is your draft-first rule, enforced at the tool level. Read-only tools
(`Read`, `Glob`, `Grep`, web search) auto-run; everything that mutates is gated.

**Tip:** point `WORKSPACE_DIR` at a folder that's under git and commit before a
session, so you can always `git diff` and revert anything an agent changed.

## Customizing your roster

Everything lives in `backend/roster.py`. Each agent is one entry:

```python
("bld06", "BLD-06", "build", "Migration Writer",
 "Writes and runs safe DB migrations", "build",
 "You write incremental Supabase migrations ..."),
```

- `policy="build"` → real local tools, gated by approval.
- `policy="chat"` → read-and-advise only, never mutates.

The shared context and per-cluster rules are appended automatically. Restart the
backend to pick up changes.

### Per-agent models (cost control)

Each agent runs on its own model, set in the `MODELS` map at the top of
`backend/roster.py`. Cheaper models handle simple drafting; stronger ones are
reserved for code and heavy reasoning:

- `"opus"` — most capable, priciest (~$5 / $15 per 1M tokens in:out)
- `"sonnet"` — balanced (~$3 / $15)
- `"haiku"` — cheapest and fastest (~$1 / $5)

The defaults are 2 Opus (the two main builders), 5 Sonnet, and 8 Haiku
(advisors). Edit any agent's model in that map. Set `AGENT_MODEL` in `.env` to
force **every** agent onto one model — a quick cost lockdown. Each agent's model
shows in the UI (next to its code) and in the backend terminal when its session
starts.

## Adding live connectors (optional, later)

The SDK takes MCP servers the same way the artifact did. In `backend/main.py`,
add an `mcp_servers={...}` entry to `ClaudeAgentOptions` for an agent and list
its tools in `allowed_tools` (or leave them gated). That's how you'd let an agent
read your Supabase or Gmail alongside the local filesystem.

## Notes

- Models are per-agent (see *Per-agent models* above); `AGENT_MODEL` in `.env`
  overrides all of them at once.
- Agents run concurrently — each keeps its own live session, so several can work
  at the same time and context carries across turns. Every brief, reply, tool
  call, and approval is saved to `backend/history.db` and reloaded on open.
- This is yours to run and maintain — unlike Claude Code, which Anthropic keeps
  updated. Keep `claude-agent-sdk` current with `pip install -U claude-agent-sdk`.
