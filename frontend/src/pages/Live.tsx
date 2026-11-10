import { useEffect, useRef, useState } from "react";
import { PhoneOff, Radio } from "lucide-react";
import { api, getToken, WS_BASE } from "../api";
import { can, useAuth } from "../auth";
import { Badge, Empty, Loading, Modal, PageHeader, useToast } from "../components/ui";

const since = (ts: number) => { const s = Math.max(0, Math.floor(Date.now() / 1000 - ts)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

function Monitor({ call, onClose }: { call: any; onClose: () => void }) {
  const toast = useToast();
  const [lines, setLines] = useState<any[]>([]); const [ended, setEnded] = useState<string | null>(null); const [canEnd, setCanEnd] = useState(false);
  const ws = useRef<WebSocket | null>(null); const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const s = new WebSocket(`${WS_BASE}/ws/monitor/${call.call_id}?token=${encodeURIComponent(getToken())}`);
    ws.current = s;
    s.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "transcript") setLines((l) => [...l, m]);
      else if (m.type === "hello") setCanEnd(m.can_end);
      else if (m.type === "ended") setEnded(m.reason || "ended");
    };
    s.onclose = () => setEnded((e) => e || "disconnected");
    return () => s.close();
  }, [call.call_id]);
  useEffect(() => { box.current?.scrollTo(0, box.current.scrollHeight); }, [lines]);
  const end = () => { if (confirm("End this call now?")) { ws.current?.send(JSON.stringify({ type: "end" })); toast("Ending call...", "info"); } };
  return (
    <Modal open onClose={onClose} wide title={<div className="row">{ended ? <Badge tone="gray">ended</Badge> : <span className="live-dot" />}{call.direction === "inbound" ? call.from : call.to} · {call.agent}</div>}
      footer={!ended && canEnd ? <button className="btn danger" onClick={end}><PhoneOff size={15} />End call</button> : undefined}>
      <div ref={box} className="chat" style={{ maxHeight: 460, overflowY: "auto" }}>
        {lines.map((l, i) => <div key={i} className={`bubble ${l.role}`}><small>{l.role === "user" ? "Caller" : l.role === "dtmf" ? "Keypad" : l.role}</small>{l.text}</div>)}
        {!lines.length && <div className="muted">Waiting for the conversation...</div>}
        {ended && <div className="bubble system">Call ended ({ended.replace(/_/g, " ")})</div>}
      </div>
    </Modal>
  );
}

export default function Live() {
  const { user } = useAuth();
  const [list, setList] = useState<any[] | null>(null); const [open, setOpen] = useState<any>(null); const [, tick] = useState(0);
  const load = () => api("/api/live").then(setList).catch(() => setList([]));
  useEffect(() => { load(); const t = setInterval(load, 3000); const c = setInterval(() => tick((x) => x + 1), 1000); return () => { clearInterval(t); clearInterval(c); }; }, []);
  const end = async (id: string) => { if (confirm("End this call now?")) { await api(`/api/calls/${id}/end`, { method: "POST" }); load(); } };
  if (!list) return <Loading />;
  return (
    <>
      <PageHeader title="Live Calls" subtitle="Calls in progress right now - follow the transcript live or end a call" />
      <div className="card">
        {list.length ? (
          <div className="table-wrap"><table className="table"><thead><tr><th></th><th>Agent</th><th>Direction</th><th>Caller / Callee</th><th>Provider</th><th>Status</th><th>Duration</th><th></th></tr></thead>
            <tbody>{list.map((c) => (
              <tr key={c.call_id} className="click" onClick={() => setOpen(c)}>
                <td><span className="live-dot" /></td><td><b>{c.agent}</b>{c.campaign && <div className="small muted">{c.campaign}</div>}</td><td><Badge>{c.direction}</Badge></td>
                <td className="mono">{c.direction === "inbound" ? c.from : c.to}</td><td><Badge tone="gray">{c.provider}</Badge></td><td><Badge live>{c.status}</Badge></td>
                <td className="mono">{since(c.ts)}</td>
                <td onClick={(e) => e.stopPropagation()}>{can(user, "member") && <button className="btn sm danger" onClick={() => end(c.call_id)}><PhoneOff size={13} />End</button>}</td>
              </tr>))}</tbody></table></div>
        ) : <Empty icon={<Radio size={26} />} title="No calls in progress" text="Live calls show up here as soon as they connect." />}
      </div>
      {open && <Monitor call={open} onClose={() => setOpen(null)} />}
    </>
  );
}
