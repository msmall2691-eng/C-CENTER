"""
Local command center backend.

Runs on Megan's machine. Wraps the Claude Agent SDK so each agent has real
local tools, scoped to WORKSPACE_DIR. Read/search tools auto-approve; any
file write/edit or shell command (git included) pauses and asks the UI for
approval over the WebSocket before it runs.

Agents run concurrently: each keeps its own live session (context carries
across turns), and several can be working at the same time. Every agent's
activity is also printed to this terminal so you can watch them all run.

Run:  uvicorn main:app --host 0.0.0.0 --port 8000
"""

import os
import sys
import time
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from roster import AGENTS, AGENTS_BY_ID
import store

load_dotenv()

WORKSPACE = os.environ.get("WORKSPACE_DIR") or str(Path.home())
MODEL_OVERRIDE = os.environ.get("AGENT_MODEL")  # if set, forces every agent to this model
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def model_for(agent: dict) -> str:
    return MODEL_OVERRIDE or agent.get("model") or "sonnet"

# Tools that never need approval — read-only / inspection only.
SAFE_TOOLS = {"Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite", "NotebookRead"}
# Tools that mutate the machine — always gated behind Megan's approval.
MUTATING_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"}

app = FastAPI(title="Command Center (local)")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Live terminal logging — so you can watch every agent work from this window.
# ---------------------------------------------------------------------------
_CLUSTER_ANSI = {"build": "36", "field": "33", "school": "35"}  # cyan / yellow / magenta


def log(agent: dict, icon: str, msg: str) -> None:
    code = agent["code"]
    color = _CLUSTER_ANSI.get(agent["cluster"], "37")
    line = msg.replace("\n", " ⏎ ")
    if len(line) > 160:
        line = line[:159] + "…"
    print(f"\033[90m{time.strftime('%H:%M:%S')}\033[0m "
          f"\033[1;{color}m{code:<7}\033[0m {icon} {line}", flush=True)


def banner() -> None:
    print("\n\033[1mCommand Center\033[0m — agents online, watching this terminal", flush=True)
    print(f"  workspace : {WORKSPACE}", flush=True)
    print(f"  model     : {MODEL_OVERRIDE or 'per-agent (see roster.py)'}", flush=True)
    print(f"  api key   : {'set' if API_KEY else 'MISSING — set ANTHROPIC_API_KEY in backend/.env'}", flush=True)
    print(f"  roster    : {len(AGENTS)} agents ({sum(a['policy']=='build' for a in AGENTS)} build, "
          f"{sum(a['policy']!='build' for a in AGENTS)} advisor)\n", flush=True)


@app.on_event("startup")
async def _startup():
    banner()


@app.get("/api/config")
def get_config():
    return {"workspace": WORKSPACE, "model": MODEL_OVERRIDE or "per-agent", "has_key": bool(API_KEY)}


@app.get("/api/agents")
def get_agents():
    keys = ("id", "code", "cluster", "name", "role", "policy", "model")
    return [{k: a[k] for k in keys} for a in AGENTS]


@app.get("/api/history")
def get_history():
    """Every agent's saved thread, so the UI can restore it on open."""
    return store.all_threads()


@app.get("/api/usage")
def get_usage():
    """Cumulative token + USD cost per agent and overall (the usage meter)."""
    return store.usage_summary()


@app.delete("/api/history/{agent_id}")
def delete_history(agent_id: str):
    store.clear(agent_id)
    return {"ok": True, "agent_id": agent_id}


def summarize_input(tool: str, inp: dict) -> dict:
    """Pull the human-relevant bits out of a tool call for the approval card."""
    inp = inp or {}
    if tool == "Bash":
        return {"command": inp.get("command", ""), "description": inp.get("description", "")}
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        out = {"file_path": inp.get("file_path") or inp.get("path", "")}
        if isinstance(inp.get("content"), str):
            out["preview"] = inp["content"][:800]
        if isinstance(inp.get("new_string"), str):
            out["new_string"] = inp["new_string"][:800]
        if isinstance(inp.get("old_string"), str):
            out["old_string"] = inp["old_string"][:400]
        return out
    return {k: v for k, v in inp.items() if k in ("file_path", "pattern", "path", "glob", "query", "url")}


def tool_log_label(tool: str, summary: dict) -> str:
    if tool == "Bash":
        return f"$ {summary.get('command', '')}"
    if summary.get("file_path"):
        return f"{tool} {summary['file_path']}"
    for k in ("pattern", "query", "url", "path"):
        if summary.get(k):
            return f"{tool} {summary[k]}"
    return tool


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()

    clients: dict[str, ClaudeSDKClient] = {}          # agent_id -> live session
    agent_locks: dict[str, asyncio.Lock] = {}         # one turn at a time per agent
    pending: dict[int, asyncio.Future] = {}           # approval id -> future
    tasks: set[asyncio.Task] = set()
    counter = {"n": 0}
    send_lock = asyncio.Lock()                         # serialize concurrent sends

    async def send(obj):
        async with send_lock:
            await ws.send_json(obj)

    async def store_add(*a, **k):
        await asyncio.to_thread(store.add, *a, **k)

    async def store_usage(*a, **k):
        await asyncio.to_thread(store.record_usage, *a, **k)

    def lock_for(agent_id: str) -> asyncio.Lock:
        return agent_locks.setdefault(agent_id, asyncio.Lock())

    async def request_approval(agent, tool_name, input_data):
        counter["n"] += 1
        aid = counter["n"]
        fut = asyncio.get_event_loop().create_future()
        pending[aid] = fut
        summary = summarize_input(tool_name, input_data)
        log(agent, "\033[33m⏸\033[0m", f"awaiting approval — {tool_log_label(tool_name, summary)}")
        await send({
            "type": "approval_request", "id": aid, "agent_id": agent["id"],
            "tool": tool_name, "input": summary,
        })
        try:
            approved = await asyncio.wait_for(fut, timeout=900)
        except asyncio.TimeoutError:
            pending.pop(aid, None)
            approved = False
        if approved:
            log(agent, "\033[32m✓\033[0m", f"approved — {tool_log_label(tool_name, summary)}")
            return PermissionResultAllow()
        log(agent, "\033[31m✗\033[0m", f"declined — {tool_log_label(tool_name, summary)}")
        return PermissionResultDeny(message="Megan declined this action. Suggest an alternative or ask her what to change.")

    def can_use_tool_for(agent):
        async def cb(tool_name, input_data, context):
            if tool_name in SAFE_TOOLS:
                return PermissionResultAllow()
            if agent["policy"] != "build" and tool_name in MUTATING_TOOLS:
                return PermissionResultDeny(
                    message="This is a read-only advisor agent. Switch to a Build Crew agent to change files or run commands."
                )
            return await request_approval(agent, tool_name, input_data)
        return cb

    async def build_client(agent):
        opts = ClaudeAgentOptions(
            system_prompt=agent["system_prompt"],
            allowed_tools=sorted(SAFE_TOOLS),   # auto-approved; everything else is gated
            permission_mode="default",
            can_use_tool=can_use_tool_for(agent),
            cwd=WORKSPACE,
            model=model_for(agent),
            env={"ANTHROPIC_API_KEY": API_KEY} if API_KEY else {},
        )
        client = ClaudeSDKClient(options=opts)
        await client.connect()
        return client

    async def stream_message(agent, msg, buf, flush):
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            for block in content:
                name = type(block).__name__
                if name == "TextBlock" and getattr(block, "text", ""):
                    buf.append(block.text)
                    await send({"type": "text", "agent_id": agent["id"], "text": block.text})
                    log(agent, "\033[90m·\033[0m", block.text.strip())
                elif name == "ToolUseBlock":
                    await flush()  # persist agent text before the tool, mirroring the UI
                    tool = getattr(block, "name", "")
                    summary = summarize_input(tool, getattr(block, "input", {}) or {})
                    await store_add(agent["id"], "tool", tool=tool, inp=summary)
                    await send({"type": "tool", "agent_id": agent["id"], "tool": tool, "input": summary})
                    log(agent, "⚙", tool_log_label(tool, summary))
        if type(msg).__name__ == "ResultMessage":
            usage = getattr(msg, "usage", None) or {}
            cost = float(getattr(msg, "total_cost_usd", None) or 0.0)
            inp = int(usage.get("input_tokens", 0) or 0)
            out = int(usage.get("output_tokens", 0) or 0)
            await store_usage(agent["id"], model_for(agent), inp, out, cost)
            log(agent, "$", f"{model_for(agent)} · {inp + out} tok · ${cost:.4f}")
            await send({"type": "result", "agent_id": agent["id"],
                        "cost": cost, "input": inp, "output": out})

    async def run_turn(agent, text):
        """One full turn for one agent. Serialized per agent, concurrent across agents."""
        async with lock_for(agent["id"]):
            buf: list[str] = []

            async def flush():
                if buf:
                    await store_add(agent["id"], "agent", text="".join(buf))
                    buf.clear()

            try:
                client = clients.get(agent["id"])
                if client is None:
                    client = await build_client(agent)
                    clients[agent["id"]] = client
                    log(agent, "\033[32m●\033[0m", f"session started · {model_for(agent)}")
                await send({"type": "start", "agent_id": agent["id"]})
                log(agent, "▸", text.strip())
                await client.query(text)
                async for msg in client.receive_response():
                    await stream_message(agent, msg, buf, flush)
                await flush()
                await send({"type": "end", "agent_id": agent["id"]})
            except Exception as e:
                await flush()
                await store_add(agent["id"], "error", text=str(e))
                log(agent, "\033[31m✗\033[0m", f"error — {e}")
                await send({"type": "error", "agent_id": agent["id"], "text": str(e)})
                await send({"type": "end", "agent_id": agent["id"]})
                # reset this agent's session so a later message starts clean
                bad = clients.pop(agent["id"], None)
                if bad:
                    try:
                        await bad.disconnect()
                    except Exception:
                        pass

    def spawn(coro):
        t = asyncio.create_task(coro)
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    try:
        while True:
            data = await ws.receive_json()
            kind = data.get("type")

            if kind == "approval":
                fut = pending.pop(data.get("id"), None)
                if fut and not fut.done():
                    fut.set_result(bool(data.get("approved")))
                continue

            if kind == "clear":
                aid = data.get("agent_id")
                bad = clients.pop(aid, None)        # forget the live session…
                if bad:
                    try:
                        await bad.disconnect()
                    except Exception:
                        pass
                await asyncio.to_thread(store.clear, aid)   # …and the saved thread
                await send({"type": "cleared", "agent_id": aid})
                continue

            if kind != "message":
                continue

            agent = AGENTS_BY_ID.get(data.get("agent_id"))
            if not agent:
                await send({"type": "error", "agent_id": data.get("agent_id"), "text": "Unknown agent."})
                continue

            await store_add(agent["id"], "user", text=data.get("text", ""))  # remember the brief

            if not API_KEY:
                msg = "No ANTHROPIC_API_KEY found. Add it to backend/.env and restart."
                await store_add(agent["id"], "error", text=msg)
                await send({"type": "error", "agent_id": agent["id"], "text": msg})
                await send({"type": "end", "agent_id": agent["id"]})
                continue

            # Fire the turn as its own task so other agents keep running.
            spawn(run_turn(agent, data.get("text", "")))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        for client in clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass


@app.get("/api/health")
def health():
    return {"ok": True, "agents": len(AGENTS), "workspace": WORKSPACE}


# Serve the built frontend (frontend/dist) from the backend when it exists, so a
# single always-on process serves both the UI and the API on one port. In dev you
# can still use the Vite server on :5173 (run ./start.sh); this mount is a no-op
# until you `npm run build`. Mounted last so /api/* and /ws match first.
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="ui")
