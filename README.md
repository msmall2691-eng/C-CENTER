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

## Adding live connectors (optional, later)

The SDK takes MCP servers the same way the artifact did. In `backend/main.py`,
add an `mcp_servers={...}` entry to `ClaudeAgentOptions` for an agent and list
its tools in `allowed_tools` (or leave them gated). That's how you'd let an agent
read your Supabase or Gmail alongside the local filesystem.

## Notes

- Model is set by `AGENT_MODEL` in `.env` (`sonnet`, `opus`, `haiku`, or a full id).
- The backend keeps one live session per agent so context carries across turns;
  switching agents starts a fresh session.
- This is yours to run and maintain — unlike Claude Code, which Anthropic keeps
  updated. Keep `claude-agent-sdk` current with `pip install -U claude-agent-sdk`.
