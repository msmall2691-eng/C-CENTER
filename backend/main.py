"""
Local command center backend.

Runs on Megan's machine. Wraps the Claude Agent SDK so each agent has real
local tools, scoped to WORKSPACE_DIR. Read/search tools auto-approve; any
file write/edit or shell command (git included) pauses and asks the UI for
approval over the WebSocket before it runs.

Run:  uvicorn main:app --reload --port 8000
"""

import os
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


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()

    pending: dict[int, asyncio.Future] = {}
    inbound: asyncio.Queue = asyncio.Queue()
    counter = {"n": 0}
    state = {"client": None, "agent_id": None}

    async def send(obj):
        await ws.send_json(obj)

    async def reader():
        try:
            while True:
                data = await ws.receive_json()
                if data.get("type") == "approval":
                    fut = pending.pop(data.get("id"), None)
                    if fut and not fut.done():
                        fut.set_result(bool(data.get("approved")))
                else:
                    await inbound.put(data)
        except (WebSocketDisconnect, RuntimeError):
            await inbound.put({"type": "_disconnect"})
        except Exception:
            await inbound.put({"type": "_disconnect"})

    reader_task = asyncio.create_task(reader())

    async def request_approval(tool_name, input_data):
        counter["n"] += 1
        aid = counter["n"]
        fut = asyncio.get_event_loop().create_future()
        pending[aid] = fut
        await send({
            "type": "approval_request", "id": aid,
            "tool": tool_name, "input": summarize_input(tool_name, input_data),
        })
        try:
            approved = await asyncio.wait_for(fut, timeout=900)
        except asyncio.TimeoutError:
            pending.pop(aid, None)
            approved = False
        if approved:
            return PermissionResultAllow()
        return PermissionResultDeny(message="Megan declined this action. Suggest an alternative or ask her what to change.")

    def can_use_tool_for(policy):
        async def cb(tool_name, input_data, context):
            if tool_name in SAFE_TOOLS:
                return PermissionResultAllow()
            if policy != "build" and tool_name in MUTATING_TOOLS:
                return PermissionResultDeny(
                    message="This is a read-only advisor agent. Switch to a Build Crew agent to change files or run commands."
                )
            return await request_approval(tool_name, input_data)
        return cb

    async def build_client(agent):
        opts = ClaudeAgentOptions(
            system_prompt=agent["system_prompt"],
            allowed_tools=sorted(SAFE_TOOLS),   # auto-approved; everything else is gated
            permission_mode="default",
            can_use_tool=can_use_tool_for(agent["policy"]),
            cwd=WORKSPACE,
            model=MODEL,
            env={"ANTHROPIC_API_KEY": API_KEY} if API_KEY else {},
        )
        client = ClaudeSDKClient(options=opts)
        await client.connect()
        return client

    async def stream_message(msg):
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            for block in content:
                name = type(block).__name__
                if name == "TextBlock" and getattr(block, "text", ""):
                    await send({"type": "text", "text": block.text})
                elif name == "ToolUseBlock":
                    tool = getattr(block, "name", "")
                    await send({"type": "tool", "tool": tool,
                                "input": summarize_input(tool, getattr(block, "input", {}) or {})})
        if type(msg).__name__ == "ResultMessage":
            await send({"type": "result"})

    try:
        while True:
            data = await inbound.get()
            kind = data.get("type")
            if kind == "_disconnect":
                break
            if kind != "message":
                continue

            agent = AGENTS_BY_ID.get(data.get("agent_id"))
            if not agent:
                await send({"type": "error", "text": "Unknown agent."})
                continue

            if not API_KEY:
                await send({"type": "error", "text": "No ANTHROPIC_API_KEY found. Add it to backend/.env and restart."})
                await send({"type": "end"})
                continue

            try:
                if state["agent_id"] != agent["id"]:
                    if state["client"]:
                        await state["client"].disconnect()
                    state["client"] = await build_client(agent)
                    state["agent_id"] = agent["id"]
                client = state["client"]

                await send({"type": "start"})
                await client.query(data.get("text", ""))
                async for msg in client.receive_response():
                    await stream_message(msg)
                await send({"type": "end"})
            except Exception as e:
                await send({"type": "error", "text": str(e)})
                await send({"type": "end"})
                # reset the session so a later message starts clean
                if state["client"]:
                    try:
                        await state["client"].disconnect()
                    except Exception:
                        pass
                state["client"] = None
                state["agent_id"] = None
    finally:
        reader_task.cancel()
        if state["client"]:
            try:
                await state["client"].disconnect()
            except Exception:
                pass


@app.get("/")
def root():
    return {"ok": True, "agents": len(AGENTS), "workspace": WORKSPACE}
