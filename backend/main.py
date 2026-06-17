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

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from roster import AGENTS, AGENTS_BY_ID

load_dotenv()

WORKSPACE = os.environ.get("WORKSPACE_DIR") or str(Path.home())
MODEL = os.environ.get("AGENT_MODEL", "sonnet")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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
    print(f"  model     : {MODEL}", flush=True)
    print(f"  api key   : {'set' if API_KEY else 'MISSING — set ANTHROPIC_API_KEY in backend/.env'}", flush=True)
    print(f"  roster    : {len(AGENTS)} agents ({sum(a['policy']=='build' for a in AGENTS)} build, "
          f"{sum(a['policy']!='build' for a in AGENTS)} advisor)\n", flush=True)


@app.on_event("startup")
async def _startup():
    banner()


@app.get("/api/config")
def get_config():
    return {"workspace": WORKSPACE, "model": MODEL, "has_key": bool(API_KEY)}


@app.get("/api/agents")
def get_agents():
    keys = ("id", "code", "cluster", "name", "role", "policy")
    return [{k: a[k] for k in keys} for a in AGENTS]


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
            model=MODEL,
            env={"ANTHROPIC_API_KEY": API_KEY} if API_KEY else {},
        )
        client = ClaudeSDKClient(options=opts)
        await client.connect()
        return client

    async def stream_message(agent, msg):
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            for block in content:
                name = type(block).__name__
                if name == "TextBlock" and getattr(block, "text", ""):
                    await send({"type": "text", "agent_id": agent["id"], "text": block.text})
                    log(agent, "\033[90m·\033[0m", block.text.strip())
                elif name == "ToolUseBlock":
                    tool = getattr(block, "name", "")
                    summary = summarize_input(tool, getattr(block, "input", {}) or {})
                    await send({"type": "tool", "agent_id": agent["id"], "tool": tool, "input": summary})
                    log(agent, "⚙", tool_log_label(tool, summary))
        if type(msg).__name__ == "ResultMessage":
            await send({"type": "result", "agent_id": agent["id"]})

    async def run_turn(agent, text):
        """One full turn for one agent. Serialized per agent, concurrent across agents."""
        async with lock_for(agent["id"]):
            try:
                client = clients.get(agent["id"])
                if client is None:
                    client = await build_client(agent)
                    clients[agent["id"]] = client
                    log(agent, "\033[32m●\033[0m", "session started")
                await send({"type": "start", "agent_id": agent["id"]})
                log(agent, "▸", text.strip())
                await client.query(text)
                async for msg in client.receive_response():
                    await stream_message(agent, msg)
                await send({"type": "end", "agent_id": agent["id"]})
            except Exception as e:
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

            if kind != "message":
                continue

            agent = AGENTS_BY_ID.get(data.get("agent_id"))
            if not agent:
                await send({"type": "error", "agent_id": data.get("agent_id"), "text": "Unknown agent."})
                continue
            if not API_KEY:
                await send({"type": "error", "agent_id": agent["id"],
                            "text": "No ANTHROPIC_API_KEY found. Add it to backend/.env and restart."})
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


@app.get("/")
def root():
    return {"ok": True, "agents": len(AGENTS), "workspace": WORKSPACE}
