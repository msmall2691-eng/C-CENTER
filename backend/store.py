"""
Conversation memory for the command center.

Every brief, agent reply, tool call, and error is written to a local SQLite
file so nothing is lost when you refresh the UI or restart the backend. On
open, the frontend reloads each agent's thread straight from here.

This is *your* archive on *your* machine — the Anthropic API processes messages
to generate replies but does not keep a retrievable history, so this file is the
only durable record of what your agents have done.

The DB lives at backend/history.db by default (override with HISTORY_DB).
"""

import os
import json
import time
import sqlite3
import threading
from pathlib import Path

DB_PATH = os.environ.get("HISTORY_DB") or str(Path(__file__).parent / "history.db")

_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.execute(
    """CREATE TABLE IF NOT EXISTS messages (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ts        TEXT NOT NULL,
        agent_id  TEXT NOT NULL,
        kind      TEXT NOT NULL,        -- user | agent | tool | error
        text      TEXT,
        tool      TEXT,
        input     TEXT                  -- JSON, for tool calls
    )"""
)
_conn.execute("CREATE INDEX IF NOT EXISTS idx_agent ON messages(agent_id, id)")
_conn.execute(
    """CREATE TABLE IF NOT EXISTS usage (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ts            TEXT NOT NULL,
        agent_id      TEXT NOT NULL,
        model         TEXT,
        input_tokens  INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        cost_usd      REAL DEFAULT 0
    )"""
)
_conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_agent ON usage(agent_id)")
_conn.commit()


def add(agent_id, kind, text=None, tool=None, inp=None):
    """Append one item to an agent's thread. Mirrors a frontend thread item."""
    with _lock:
        _conn.execute(
            "INSERT INTO messages(ts, agent_id, kind, text, tool, input) VALUES (?,?,?,?,?,?)",
            (time.strftime("%Y-%m-%dT%H:%M:%S"), agent_id, kind, text, tool,
             json.dumps(inp) if inp is not None else None),
        )
        _conn.commit()


def _row_to_item(kind, text, tool, inp):
    item = {"kind": kind}
    if text is not None:
        item["text"] = text
    if tool is not None:
        item["tool"] = tool
    if inp is not None:
        item["input"] = json.loads(inp)
    return item


def history(agent_id, limit=1000):
    """Most recent `limit` items for one agent, oldest-first (display order)."""
    with _lock:
        rows = _conn.execute(
            "SELECT kind, text, tool, input FROM messages WHERE agent_id=? ORDER BY id DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
    return [_row_to_item(*r) for r in reversed(rows)]


def all_threads(limit_per=1000):
    """{agent_id: [items]} for every agent that has any saved history."""
    with _lock:
        ids = [r[0] for r in _conn.execute("SELECT DISTINCT agent_id FROM messages").fetchall()]
    return {aid: history(aid, limit_per) for aid in ids}


def clear(agent_id):
    """Wipe one agent's saved thread (history only; usage is kept)."""
    with _lock:
        _conn.execute("DELETE FROM messages WHERE agent_id=?", (agent_id,))
        _conn.commit()


def record_usage(agent_id, model, input_tokens, output_tokens, cost_usd):
    """Append one turn's token + cost usage (cost from the SDK's total_cost_usd)."""
    with _lock:
        _conn.execute(
            "INSERT INTO usage(ts, agent_id, model, input_tokens, output_tokens, cost_usd) "
            "VALUES (?,?,?,?,?,?)",
            (time.strftime("%Y-%m-%dT%H:%M:%S"), agent_id, model,
             int(input_tokens or 0), int(output_tokens or 0), float(cost_usd or 0.0)),
        )
        _conn.commit()


def usage_summary():
    """{total, agents} — cumulative turns, tokens, and USD cost, per agent + overall."""
    with _lock:
        rows = _conn.execute(
            "SELECT agent_id, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd) "
            "FROM usage GROUP BY agent_id"
        ).fetchall()
        tot = _conn.execute(
            "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd) FROM usage"
        ).fetchone()
    agents = {
        aid: {"turns": turns, "input": inp or 0, "output": out or 0, "cost": cost or 0.0}
        for (aid, turns, inp, out, cost) in rows
    }
    return {
        "agents": agents,
        "total": {"turns": tot[0] or 0, "input": tot[1] or 0,
                  "output": tot[2] or 0, "cost": tot[3] or 0.0},
    }
