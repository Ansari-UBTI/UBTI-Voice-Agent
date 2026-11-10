import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Download, PhoneCall, RefreshCw, Search, Trash2 } from "lucide-react";
import { api, API_BASE, fmtDate, fmtDur, money, reason, REASONS } from "../api";
import { can, useAuth } from "../auth";
import { Badge, Empty, Loading, Modal, PageHeader, useToast } from "../components/ui";

function Recording({ callId }: { callId: string }) {
  const [url, setUrl] = useState(""); const [err, setErr] = useState("");
  useEffect(() => {
    setUrl(""); setErr("");
    api(`/api/calls/${callId}/recording-url`).then((r) => setUrl(r.url.startsWith("/") ? API_BASE + r.url : r.url)).catch((e) => setErr(e.message));
  }, [callId]);
  if (err) return <div className="small muted">{err}</div>;
  return url ? <div className="stack" style={{ gap: 4 }}><audio controls preload="metadata" src={url} /><span className="small faint">Left channel: caller · Right channel: agent</span></div> : <div className="small muted">Loading recording...</div>;
}

const show = (v: any) => (v === null || v === undefined || v === "" ? "-" : typeof v === "boolean" ? (v ? "Yes" : "No") : String(v));

export function CallDetail({ call, onClose, onUpdate }: { call: any; onClose: () => void; onUpdate?: (c: any) => void }) {
  const toast = useToast(); const { user } = useAuth(); const [busy, setBusy] = useState(false);
  const custom = call?.analysis?.custom || {};
  const rerun = async () => {
    setBusy(true);
    try { const c = await api(`/api/calls/${call.id}/analyze`, { method: "POST" }); onUpdate?.(c); toast("Analysis updated", "success"); }
    catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  return (
    <Modal open={!!call} onClose={onClose} wide title={<div className="row">Call details <Badge>{call?.status}</Badge>{call?.agent_version && <Badge tone="gray">{`agent v${call.agent_version}`}</Badge>}</div>}>
      {call && <>
        <div className="kpi-row">
          {[["Agent", call.agent_name || call.tenant_name || "-"], ["Direction", call.direction], ["From", call.from_number], ["To", call.to_number],
            ["Started", fmtDate(call.started_at)], ["Duration", fmtDur(call.duration_sec)], ["Ended by", reason(call.ended_reason)], ["Provider", call.provider],
            ["AI cost", money(call.cost_ai_usd, 4)], ["Telephony", money(call.cost_telephony_usd, 4)], ["Total cost", money(call.cost_usd, 4)],
            ...(call.transferred ? [["Transferred to", call.transfer_target]] : [])].map(([k, v]) => (
            <div key={k}><div className="small">{k}</div><b>{v}</b></div>))}
        </div>
        {call.has_recording && <Recording callId={call.id} />}
        {(call.summary || call.sentiment) && (
          <div className="card card-pad" style={{ background: "var(--primary-50)", boxShadow: "none" }}>
            <div className="row wrap" style={{ marginBottom: 6 }}><b>AI summary</b>{call.sentiment && <Badge>{call.sentiment}</Badge>}{call.outcome && <Badge tone="violet">{call.outcome}</Badge>}{call.language && <Badge tone="gray">{call.language}</Badge>}
              {call.call_successful !== null && call.call_successful !== undefined && <Badge tone={call.call_successful ? "green" : "red"}>{call.call_successful ? "successful" : "not successful"}</Badge>}
              {call.in_voicemail && <Badge tone="amber">voicemail</Badge>}</div>
            <div style={{ lineHeight: 1.6 }}>{call.summary}</div>
          </div>)}
        {Object.keys(custom).length > 0 && <>
          <b>Extracted data</b>
          <div className="kpi-row">{Object.entries(custom).map(([k, v]) => <div key={k}><div className="small mono">{k}</div><b>{show(v)}</b></div>)}</div>
        </>}
        {Object.keys(call.variables || {}).length > 0 && <div className="row wrap" style={{ gap: 6 }}><span className="small muted">Variables:</span>{Object.entries(call.variables).map(([k, v]) => <span key={k} className="chip">{k}={String(v)}</span>)}</div>}
        {call.error && <div className="small" style={{ color: "var(--danger)" }}>Error: {call.error}</div>}
        <div className="row between"><b>Transcript</b>{can(user, "member") && call.status === "completed" && <button className="btn sm" disabled={busy} onClick={rerun}><RefreshCw size={13} />{busy ? "Analysing..." : "Re-run analysis"}</button>}</div>
        <div className="chat">
          {call.transcript?.length ? call.transcript.map((t: any, i: number) => (
            <div key={i} className={`bubble ${t.role}`}><small>{t.role === "user" ? "Caller" : t.role === "dtmf" ? "Keypad" : t.role} · {fmtDur(Math.round(t.offset_ms / 1000))}</small>{t.text}</div>
          )) : <div className="muted">No transcript recorded.</div>}
        </div>
      </>}
    </Modal>
  );
}

export default function Calls() {
  const toast = useToast(); const [sp] = useSearchParams(); const { user } = useAuth();
  const [data, setData] = useState<any>(null); const [agents, setAgents] = useState<any[]>([]);
  const [fl, setFl] = useState({ q: "", agent_id: "", status: "", direction: "", reason: "" }); const [page, setPage] = useState(1);
  const [open, setOpen] = useState<any>(null);
  const load = () => api(`/api/calls?page=${page}&size=20&${new URLSearchParams(fl as any)}`).then(setData);
  useEffect(() => { load(); }, [page, fl.agent_id, fl.status, fl.direction, fl.reason]);
  useEffect(() => { api("/api/agents").then(setAgents); const id = sp.get("open"); if (id) api(`/api/calls/${id}`).then(setOpen); }, []);
  const exportCsv = async () => { const b: Blob = await api("/api/calls/export"); const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = "ubti-calls.csv"; a.click(); toast("Exported", "success"); };
  const pages = data ? Math.max(1, Math.ceil(data.total / 20)) : 1;

  return (
    <>
      <PageHeader title="Call History" subtitle="Every call with recording, transcript, AI analysis and cost"
        actions={<button className="btn" onClick={exportCsv}><Download size={16} />Export CSV</button>} />
      <div className="card">
        <div className="row wrap" style={{ padding: 14, borderBottom: "1px solid var(--border)" }}>
          <form className="search" style={{ flex: 1, minWidth: 200 }} onSubmit={(e) => { e.preventDefault(); setPage(1); load(); }}><Search size={15} /><input className="input" placeholder="Search number or summary" value={fl.q} onChange={(e) => setFl({ ...fl, q: e.target.value })} /></form>
          <select className="input" style={{ width: 160 }} value={fl.agent_id} onChange={(e) => setFl({ ...fl, agent_id: e.target.value })}><option value="">All agents</option>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
          <select className="input" style={{ width: 130 }} value={fl.direction} onChange={(e) => setFl({ ...fl, direction: e.target.value })}><option value="">All directions</option><option value="inbound">Inbound</option><option value="outbound">Outbound</option><option value="test">Test</option></select>
          <select className="input" style={{ width: 130 }} value={fl.status} onChange={(e) => setFl({ ...fl, status: e.target.value })}><option value="">All status</option>{["completed", "in-progress", "dialing", "failed", "no-answer", "busy"].map((s) => <option key={s}>{s}</option>)}</select>
          <select className="input" style={{ width: 150 }} value={fl.reason} onChange={(e) => setFl({ ...fl, reason: e.target.value })}><option value="">Any end reason</option>{Object.entries(REASONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        </div>
        {!data ? <Loading /> : data.items.length ? (<>
          <div className="table-wrap"><table className="table"><thead><tr><th>Date</th><th>Agent</th><th>Direction</th><th>Caller / Callee</th><th>Duration</th><th>Status</th><th>Ended by</th><th>Sentiment</th><th>Outcome</th><th>Cost</th><th></th></tr></thead>
            <tbody>{data.items.map((c: any) => (
              <tr key={c.id} className="click" onClick={() => api(`/api/calls/${c.id}`).then(setOpen)}>
                <td className="muted">{fmtDate(c.started_at)}</td><td><b>{c.agent_name || "-"}</b>{c.agent_version && <div className="small faint">v{c.agent_version}</div>}</td><td><Badge>{c.direction}</Badge></td>
                <td className="mono">{c.direction === "inbound" ? c.from_number : c.to_number}</td><td>{fmtDur(c.duration_sec)}{c.has_recording ? " 🎧" : ""}</td>
                <td><Badge live={c.status === "in-progress"}>{c.status}</Badge></td><td className="small">{c.in_voicemail ? <Badge tone="amber">voicemail</Badge> : reason(c.ended_reason)}</td>
                <td>{c.sentiment ? <Badge>{c.sentiment}</Badge> : "-"}</td><td className="small">{c.outcome || "-"}</td><td className="small mono">{money(c.cost_usd)}</td>
                <td>{can(user, "member") && <button className="btn icon sm danger" onClick={async (e) => { e.stopPropagation(); if (confirm("Delete call, transcript and recording?")) { await api(`/api/calls/${c.id}`, { method: "DELETE" }); load(); } }}><Trash2 size={14} /></button>}</td>
              </tr>))}</tbody></table></div>
          <div className="row between" style={{ padding: 14 }}><span className="small muted">{data.total} calls</span>
            <div className="row"><button className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</button><span className="small">{page} / {pages}</span><button className="btn sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button></div></div>
        </>) : <Empty icon={<PhoneCall size={26} />} title="No calls found" />}
      </div>
      <CallDetail call={open} onClose={() => setOpen(null)} onUpdate={setOpen} />
    </>
  );
}
