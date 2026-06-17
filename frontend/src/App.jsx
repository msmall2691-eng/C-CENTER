import React, { useState, useEffect, useRef } from "react";
import {
  Boxes, Send, ChevronLeft, Check, X, AlertTriangle, Terminal, FileEdit, Eye,
  FolderGit2, Hammer, ClipboardList, GraduationCap, ShieldQuestion, Circle, Trash2, Gauge,
} from "lucide-react";

const fmtCost = (c) => "$" + ((c || 0) < 1 ? (c || 0).toFixed(4) : (c || 0).toFixed(2));

// Resolve the backend address so the UI works across machines without a rebuild.
// Priority:
//   1. ?backend=host:port in the URL (remembered in localStorage for next time)
//   2. VITE_BACKEND set at build time
//   3. the same host this page was opened from, on port 8000
// So opening http://<server-ip>:5173 from your laptop or phone automatically
// talks to the backend on <server-ip>:8000 — no code change needed.
function resolveBackend() {
  const qs = new URLSearchParams(window.location.search);
  if (qs.get("backend")) localStorage.setItem("cc_backend", qs.get("backend"));
  const override = qs.get("backend") || localStorage.getItem("cc_backend");
  let host = override || import.meta.env.VITE_BACKEND || `${window.location.hostname}:8000`;
  host = host.replace(/^https?:\/\//, "").replace(/^wss?:\/\//, "").replace(/\/+$/, "");
  const secure = window.location.protocol === "https:";
  return {
    API: `${secure ? "https" : "http"}://${host}`,
    WS: `${secure ? "wss" : "ws"}://${host}/ws`,
  };
}
const { API, WS } = resolveBackend();

const C = {
  bg: "#0F1714", panel: "#15201B", raised: "#1C2A24", raisedHi: "#22332C",
  border: "#283A32", borderHi: "#37564A", text: "#E9E4D7", muted: "#8A988E", faint: "#5E6E64",
  ok: "#7FB069", danger: "#C56B5C",
};
const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
const SANS = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

const CLUSTERS = {
  build:  { label: "Build Crew",  color: "#5E9AB5", icon: Hammer,        note: "real local tools" },
  field:  { label: "Field Crew",  color: "#D08A4E", icon: ClipboardList, note: "advisor" },
  school: { label: "School Crew", color: "#A88BC9", icon: GraduationCap, note: "advisor" },
};
const ORDER = ["build", "field", "school"];
const accent = (cluster) => (CLUSTERS[cluster] || CLUSTERS.build).color;

const Eyebrow = ({ children, color = C.muted, style }) => (
  <div style={{ fontFamily: MONO, fontSize: 10.5, letterSpacing: "0.16em", textTransform: "uppercase", color, ...style }}>
    {children}
  </div>
);

const toolIcon = (tool) => {
  if (tool === "Bash") return Terminal;
  if (["Write", "Edit", "MultiEdit", "NotebookEdit"].includes(tool)) return FileEdit;
  if (["Read", "Glob", "Grep"].includes(tool)) return Eye;
  return Circle;
};

const toolLabel = (tool, input) => {
  input = input || {};
  if (tool === "Bash") return input.command || "shell command";
  if (input.file_path) return `${tool} ${input.file_path}`;
  if (input.pattern) return `${tool} ${input.pattern}`;
  if (input.query) return `${tool} "${input.query}"`;
  return tool;
};

export default function App() {
  const [agents, setAgents] = useState([]);
  const [config, setConfig] = useState(null);
  const [conn, setConn] = useState("connecting");   // connecting | open | closed
  const [selectedId, setSelectedId] = useState(null);
  const [threads, setThreads] = useState({});        // agentId -> [items]
  const [input, setInput] = useState("");
  const [working, setWorking] = useState({});        // agentId -> currently running
  const [approvals, setApprovals] = useState({});    // agentId -> { id, tool, input }
  const [usage, setUsage] = useState({ agents: {}, total: { cost: 0, input: 0, output: 0, turns: 0 } });

  const wsRef = useRef(null);
  const appendRef = useRef({});                      // agentId -> append streaming text?
  const scrollRef = useRef(null);

  const setWorkingFor = (id, val) => setWorking((w) => ({ ...w, [id]: val }));
  const statusOf = (id) => (approvals[id] ? "approval" : working[id] ? "working" : "idle");
  const busyCount = Object.values(working).filter(Boolean).length;
  const approvalCount = Object.keys(approvals).length;
  const selected = agents.find((a) => a.id === selectedId) || null;
  const items = selectedId ? (threads[selectedId] || []) : [];

  useEffect(() => {
    fetch(`${API}/api/config`).then((r) => r.json()).then(setConfig).catch(() => {});
    fetch(`${API}/api/agents`).then((r) => r.json()).then(setAgents).catch(() => {});
    fetch(`${API}/api/history`).then((r) => r.json()).then((h) => setThreads(h || {})).catch(() => {});
    fetch(`${API}/api/usage`).then((r) => r.json()).then(setUsage).catch(() => {});
  }, []);

  const addUsage = (id, cost, input, output) =>
    setUsage((u) => {
      const a = u.agents[id] || { cost: 0, input: 0, output: 0, turns: 0 };
      return {
        agents: { ...u.agents, [id]: { cost: a.cost + cost, input: a.input + input, output: a.output + output, turns: a.turns + 1 } },
        total: { cost: u.total.cost + cost, input: u.total.input + input, output: u.total.output + output, turns: u.total.turns + 1 },
      };
    });

  useEffect(() => {
    let alive = true;
    let socket;
    const connect = () => {
      socket = new WebSocket(WS);
      wsRef.current = socket;
      setConn("connecting");
      socket.onopen = () => alive && setConn("open");
      socket.onclose = () => { if (alive) { setConn("closed"); setTimeout(connect, 1500); } };
      socket.onerror = () => socket.close();
      socket.onmessage = (e) => {
        const m = JSON.parse(e.data);
        const id = m.agent_id;
        if (m.type === "approval_request")
          return setApprovals((a) => ({ ...a, [id]: { id: m.id, tool: m.tool, input: m.input } }));
        if (!id) return;
        if (m.type === "cleared") { appendRef.current[id] = false; return setThreads((t) => ({ ...t, [id]: [] })); }
        if (m.type === "start") { appendRef.current[id] = false; return setWorkingFor(id, true); }
        if (m.type === "end") { appendRef.current[id] = false; return setWorkingFor(id, false); }
        if (m.type === "result") { if (typeof m.cost === "number") addUsage(id, m.cost, m.input || 0, m.output || 0); return; }
        if (m.type === "text") return pushText(id, m.text);
        if (m.type === "tool") { appendRef.current[id] = false; return pushItem(id, { kind: "tool", tool: m.tool, input: m.input }); }
        if (m.type === "error") { appendRef.current[id] = false; setWorkingFor(id, false); return pushItem(id, { kind: "error", text: m.text }); }
      };
    };
    connect();
    return () => { alive = false; if (socket) socket.close(); };
  }, []);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [items, working, approvals]);

  const pushItem = (id, item) =>
    setThreads((t) => ({ ...t, [id]: [...(t[id] || []), item] }));

  const pushText = (id, text) =>
    setThreads((t) => {
      const arr = [...(t[id] || [])];
      const last = arr[arr.length - 1];
      if (appendRef.current[id] && last && last.kind === "agent") {
        arr[arr.length - 1] = { ...last, text: last.text + text };
      } else {
        arr.push({ kind: "agent", text });
        appendRef.current[id] = true;
      }
      return { ...t, [id]: arr };
    });

  const send = () => {
    const text = input.trim();
    if (!text || !selected || conn !== "open" || working[selectedId]) return;
    pushItem(selectedId, { kind: "user", text });
    wsRef.current.send(JSON.stringify({ type: "message", agent_id: selectedId, text }));
    setInput("");
  };

  const decide = (agentId, approved) => {
    const ap = approvals[agentId];
    if (!ap) return;
    wsRef.current.send(JSON.stringify({ type: "approval", id: ap.id, approved }));
    setApprovals((a) => { const n = { ...a }; delete n[agentId]; return n; });
  };

  const clearThread = (agentId) => {
    if (!agentId || conn !== "open") return;
    wsRef.current.send(JSON.stringify({ type: "clear", agent_id: agentId }));
    setThreads((t) => ({ ...t, [agentId]: [] }));
    setApprovals((a) => { const n = { ...a }; delete n[agentId]; return n; });
  };

  const byCluster = (c) => agents.filter((a) => a.cluster === c);

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: C.bg, color: C.text, fontFamily: SANS }}>
      <style>{`
        textarea:focus, input:focus { outline: none; }
        .scroll::-webkit-scrollbar { width: 8px; }
        .scroll::-webkit-scrollbar-thumb { background: ${C.border}; border-radius: 8px; }
        .tk:hover { border-color: ${C.borderHi} !important; }
        @keyframes ccpulse { 0%,100%{opacity:1} 50%{opacity:.25} }
        @media (max-width: 820px){ .rail.hide{display:none} .main.hide{display:none} }
        @media (min-width: 821px){ .rail{display:flex !important} .main{display:flex !important} }
      `}</style>

      {/* header */}
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "11px 16px", borderBottom: `1px solid ${C.border}`, background: C.panel }}>
        <div style={{ display: "flex", alignItems: "center", gap: 11, minWidth: 0 }}>
          <div style={{ display: "grid", placeItems: "center", width: 32, height: 32, borderRadius: 9,
            background: C.raised, border: `1px solid ${C.border}` }}>
            <Boxes size={17} color="#5E9AB5" />
          </div>
          <div style={{ minWidth: 0 }}>
            <Eyebrow style={{ marginBottom: 2 }}>Command Center · local</Eyebrow>
            <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, color: C.muted, minWidth: 0 }}>
              <FolderGit2 size={12} color={C.faint} />
              <span style={{ fontFamily: MONO, fontSize: 11, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {config ? config.workspace : "…"}
              </span>
            </div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div title={`${usage.total.turns} turns · ${(usage.total.input + usage.total.output).toLocaleString()} tokens · estimated API cost`}
            style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Gauge size={13} color={C.muted} />
            <Eyebrow color={C.muted}>{fmtCost(usage.total.cost)}</Eyebrow>
          </div>
          {(busyCount > 0 || approvalCount > 0) && (
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              {busyCount > 0 && (
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 8, height: 8, borderRadius: 999, background: C.ok, animation: "ccpulse 1.4s ease-in-out infinite" }} />
                  <Eyebrow color={C.muted}>{busyCount} working</Eyebrow>
                </div>
              )}
              {approvalCount > 0 && (
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 8, height: 8, borderRadius: 999, background: "#D0A24E", animation: "ccpulse 1.4s ease-in-out infinite" }} />
                  <Eyebrow color="#D0A24E">{approvalCount} waiting</Eyebrow>
                </div>
              )}
            </div>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <span style={{ width: 8, height: 8, borderRadius: 999,
              background: conn === "open" ? C.ok : conn === "connecting" ? "#D0A24E" : C.danger }} />
            <Eyebrow>{conn === "open" ? "connected" : conn}</Eyebrow>
          </div>
        </div>
      </header>

      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {/* roster */}
        <aside className={"rail " + (selected ? "hide" : "")}
          style={{ display: selected ? "none" : "flex", flexDirection: "column",
            width: "100%", maxWidth: 318, borderRight: `1px solid ${C.border}` }}>
          <div className="scroll" style={{ overflowY: "auto", padding: 16 }}>
            {ORDER.map((c) => {
              const list = byCluster(c);
              if (!list.length) return null;
              const meta = CLUSTERS[c];
              return (
                <div key={c} style={{ marginBottom: 22 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 11, paddingLeft: 2 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 999, background: meta.color }} />
                    <Eyebrow color={C.text}>{meta.label}</Eyebrow>
                    <span style={{ fontFamily: MONO, fontSize: 10, color: C.faint }}>{meta.note}</span>
                  </div>
                  {list.map((a) => {
                    const Ic = (CLUSTERS[a.cluster] || {}).icon || Circle;
                    const on = a.id === selectedId;
                    return (
                      <button key={a.id} className="tk" onClick={() => setSelectedId(a.id)}
                        style={{ display: "flex", width: "100%", textAlign: "left", marginBottom: 8,
                          background: on ? C.raisedHi : C.raised, border: `1px solid ${on ? C.borderHi : C.border}`,
                          borderRadius: 10, overflow: "hidden", cursor: "pointer" }}>
                        <span style={{ width: 3, background: meta.color }} />
                        <span style={{ display: "flex", gap: 11, alignItems: "flex-start", padding: "11px 12px", minWidth: 0, flex: 1 }}>
                          <span style={{ display: "grid", placeItems: "center", width: 30, height: 30, borderRadius: 8,
                            background: C.panel, border: `1px solid ${C.border}`, marginTop: 1, flexShrink: 0 }}>
                            <Ic size={15} color={meta.color} />
                          </span>
                          <span style={{ minWidth: 0, flex: 1 }}>
                            <Eyebrow color={meta.color} style={{ marginBottom: 3 }}>{a.code}{a.model ? ` · ${a.model}` : ""}</Eyebrow>
                            <div style={{ fontSize: 13.5, fontWeight: 600, lineHeight: 1.25 }}>{a.name}</div>
                            <div style={{ fontSize: 11.5, color: C.muted, lineHeight: 1.35, marginTop: 2 }}>{a.role}</div>
                          </span>
                          <StatusDot status={statusOf(a.id)} />
                        </span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
            {!agents.length && (
              <div style={{ fontSize: 12.5, color: C.faint, lineHeight: 1.6 }}>
                No agents loaded. Is the backend running on :8000?
              </div>
            )}
          </div>
        </aside>

        {/* main */}
        <main className={"main " + (selected ? "" : "hide")}
          style={{ display: selected ? "flex" : "none", flexDirection: "column", flex: 1, minWidth: 0 }}>
          {!selected ? null : (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 16px",
                borderBottom: `1px solid ${C.border}`, background: C.panel }}>
                <button onClick={() => setSelectedId(null)}
                  style={{ display: "grid", placeItems: "center", width: 32, height: 32, borderRadius: 8,
                    border: `1px solid ${C.border}`, background: C.raised, color: C.muted, cursor: "pointer" }}>
                  <ChevronLeft size={16} />
                </button>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <Eyebrow color={accent(selected.cluster)}>{selected.code} · {selected.model} · {selected.policy === "build" ? "local tools" : "advisor"}</Eyebrow>
                  <div style={{ fontSize: 14.5, fontWeight: 700, lineHeight: 1.15 }}>{selected.name}</div>
                </div>
                <div title={`${selected.name}: ${usage.agents[selectedId]?.turns || 0} turns · ${((usage.agents[selectedId]?.input || 0) + (usage.agents[selectedId]?.output || 0)).toLocaleString()} tokens`}
                  style={{ display: "flex", alignItems: "center", gap: 6, marginRight: 2, flexShrink: 0 }}>
                  <Gauge size={13} color={C.faint} />
                  <Eyebrow color={C.muted}>{fmtCost(usage.agents[selectedId]?.cost || 0)}</Eyebrow>
                </div>
                {items.length > 0 && (
                  <button title="Clear saved history & reset this agent's memory"
                    onClick={() => { if (window.confirm(`Clear ${selected.name}'s saved history and reset its memory? This can't be undone.`)) clearThread(selected.id); }}
                    style={{ display: "grid", placeItems: "center", width: 32, height: 32, borderRadius: 8,
                      border: `1px solid ${C.border}`, background: C.raised, color: C.faint, cursor: "pointer", flexShrink: 0 }}>
                    <Trash2 size={15} />
                  </button>
                )}
              </div>

              <div ref={scrollRef} className="scroll" style={{ flex: 1, overflowY: "auto", padding: "18px 16px" }}>
                {items.length === 0 && (
                  <div style={{ padding: 16, borderRadius: 12, background: C.raised, border: `1px solid ${C.border}`, marginBottom: 14 }}>
                    <div style={{ fontSize: 13.5, color: C.text, lineHeight: 1.55 }}>{selected.role}.</div>
                    <div style={{ fontSize: 12, color: C.muted, marginTop: 8, lineHeight: 1.5 }}>
                      {selected.policy === "build"
                        ? "Reads and searches your workspace freely. Writes, edits, and commands wait for your approval."
                        : "Reads and advises only — it won't change files or run commands."}
                    </div>
                  </div>
                )}

                {items.map((it, i) => <Item key={i} it={it} cluster={selected.cluster} />)}

                {approvals[selectedId] && (
                  <ApprovalCard approval={approvals[selectedId]} onDecide={(ok) => decide(selectedId, ok)} />
                )}

                {working[selectedId] && !approvals[selectedId] && (
                  <div style={{ display: "flex", alignItems: "center", gap: 8, color: C.muted, fontSize: 12.5, paddingLeft: 2 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 999, background: accent(selected.cluster) }} />
                    <span style={{ fontFamily: MONO }}>working…</span>
                  </div>
                )}
              </div>

              <div style={{ padding: 14, borderTop: `1px solid ${C.border}`, background: C.panel }}>
                <div style={{ display: "flex", gap: 10, alignItems: "flex-end" }}>
                  <textarea value={input} onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                    rows={1} placeholder={conn !== "open" ? "Connecting to backend…"
                      : working[selectedId] ? `${selected.name} is working…` : `Brief ${selected.name}…`}
                    style={{ flex: 1, resize: "none", maxHeight: 160, minHeight: 44, padding: "12px 14px", borderRadius: 11,
                      border: `1px solid ${C.border}`, background: C.raised, color: C.text, fontSize: 14, lineHeight: 1.4, fontFamily: SANS }} />
                  <button onClick={send} disabled={!input.trim() || conn !== "open" || working[selectedId]}
                    style={{ display: "grid", placeItems: "center", width: 44, height: 44, borderRadius: 11, border: "none",
                      background: input.trim() && conn === "open" && !working[selectedId] ? accent(selected.cluster) : C.raised,
                      color: input.trim() && conn === "open" && !working[selectedId] ? C.bg : C.faint,
                      cursor: input.trim() && conn === "open" && !working[selectedId] ? "pointer" : "default" }}>
                    <Send size={17} />
                  </button>
                </div>
                <div style={{ fontSize: 10.5, color: C.faint, fontFamily: MONO, marginTop: 8 }}>
                  {selected.policy === "build"
                    ? "Every write, edit, and command waits for your approval before it runs."
                    : "Read-only advisor — drafts for you to act on."}
                </div>
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

function StatusDot({ status }) {
  if (status === "idle") return null;
  const isApproval = status === "approval";
  const color = isApproval ? "#D0A24E" : C.ok;
  return (
    <span title={isApproval ? "waiting for your approval" : "working"}
      style={{ alignSelf: "center", flexShrink: 0, width: 9, height: 9, borderRadius: 999,
        background: color, boxShadow: `0 0 0 3px ${color}22`, animation: "ccpulse 1.4s ease-in-out infinite" }} />
  );
}

function Item({ it, cluster }) {
  if (it.kind === "user") {
    return (
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 14 }}>
        <div style={{ maxWidth: "82%", padding: "11px 14px", borderRadius: "12px 12px 3px 12px",
          background: C.raisedHi, border: `1px solid ${C.border}`, fontSize: 14, lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
          {it.text}
        </div>
      </div>
    );
  }
  if (it.kind === "tool") {
    const Ic = toolIcon(it.tool);
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 0 12px 2px",
        padding: "7px 11px", borderRadius: 9, background: C.panel, border: `1px solid ${C.border}`, width: "fit-content", maxWidth: "100%" }}>
        <Ic size={13} color={accent(cluster)} />
        <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 460 }}>
          {toolLabel(it.tool, it.input)}
        </span>
      </div>
    );
  }
  if (it.kind === "error") {
    return (
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <span style={{ width: 3, borderRadius: 3, background: C.danger, alignSelf: "stretch" }} />
        <div style={{ fontSize: 13.5, color: C.danger, lineHeight: 1.55 }}>
          <AlertTriangle size={13} style={{ display: "inline", marginRight: 6, verticalAlign: "-1px" }} />
          {it.text}
        </div>
      </div>
    );
  }
  // agent text
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 16 }}>
      <span style={{ width: 3, borderRadius: 3, background: accent(cluster), alignSelf: "stretch", flexShrink: 0 }} />
      <div style={{ minWidth: 0, fontSize: 14, lineHeight: 1.6, whiteSpace: "pre-wrap", color: C.text }}>{it.text}</div>
    </div>
  );
}

function ApprovalCard({ approval, onDecide }) {
  const { tool, input } = approval;
  const isBash = tool === "Bash";
  const detail = isBash
    ? (input.command || "")
    : (input.file_path || "") + (input.new_string ? "\n\n" + input.new_string : input.preview ? "\n\n" + input.preview : "");
  return (
    <div style={{ marginBottom: 16, borderRadius: 12, border: `1px solid #D0A24E`, background: C.raised, overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 14px", borderBottom: `1px solid ${C.border}`, background: C.raisedHi }}>
        <ShieldQuestion size={15} color="#D0A24E" />
        <Eyebrow color="#D0A24E">Approval needed</Eyebrow>
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>· {isBash ? "run command" : tool}</span>
      </div>
      <pre style={{ margin: 0, padding: "12px 14px", fontFamily: MONO, fontSize: 12, lineHeight: 1.5, color: C.text,
        whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 280, overflow: "auto" }}>{detail || "(no preview)"}</pre>
      <div style={{ display: "flex", gap: 8, padding: "10px 14px", borderTop: `1px solid ${C.border}`, justifyContent: "flex-end" }}>
        <button onClick={() => onDecide(false)}
          style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 14px", borderRadius: 9,
            border: `1px solid ${C.border}`, background: "transparent", color: C.danger, cursor: "pointer", fontSize: 13, fontWeight: 600 }}>
          <X size={14} /> Decline
        </button>
        <button onClick={() => onDecide(true)}
          style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 16px", borderRadius: 9,
            border: "none", background: C.ok, color: C.bg, cursor: "pointer", fontSize: 13, fontWeight: 700 }}>
          <Check size={14} /> Approve & run
        </button>
      </div>
    </div>
  );
}
