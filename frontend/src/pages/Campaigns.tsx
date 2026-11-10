import { useEffect, useRef, useState } from "react";
import { Megaphone, Pause, Play, Plus, RotateCcw, Square, Trash2, Upload } from "lucide-react";
import { api, ago, fmtDur, reason } from "../api";
import { can, useAuth } from "../auth";
import { Badge, Empty, Field, Loading, Modal, PageHeader, Toggle, useToast } from "../components/ui";

/** CSV with a header row: phone column (phone/number/mobile) + name + any other columns -> {{variables}}.
 *  Without a header: phone,name per line. */
function parseCsv(t: string) {
  const split = (l: string) => { const out: string[] = []; let cur = "", q = false;
    for (const ch of l) { if (ch === '"') q = !q; else if (ch === "," && !q) { out.push(cur.trim()); cur = ""; } else cur += ch; }
    out.push(cur.trim()); return out; };
  const rows = t.split(/\r?\n/).filter((l) => l.trim()).map(split);
  if (!rows.length) return { contacts: [], columns: [] as string[] };
  const first = rows[0];
  const hasHeader = !/\d{6,}/.test((first[0] || "").replace(/\D/g, "")) || first.some((c) => /^(phone|number|mobile|name)$/i.test(c));
  const header = hasHeader ? first.map((h) => h.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_|_$/g, "")) : ["phone", "name"];
  const pi = Math.max(0, header.findIndex((h) => ["phone", "number", "mobile", "phone_number", "to"].includes(h)));
  const ni = header.findIndex((h) => ["name", "customer_name", "full_name"].includes(h));
  const contacts = (hasHeader ? rows.slice(1) : rows).filter((r) => /\d{6,}/.test((r[pi] || "").replace(/\D/g, ""))).map((r) => {
    const variables: Record<string, string> = {};
    header.forEach((h, i) => { if (i !== pi && h && r[i]) variables[h] = r[i]; });
    return { phone: "+" + r[pi].replace(/\D/g, ""), name: ni >= 0 ? r[ni] || null : null, variables };
  });
  return { contacts, columns: header.filter((_, i) => i !== pi) };
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function Campaigns() {
  const toast = useToast(); const { user } = useAuth(); const edit = can(user, "member");
  const [list, setList] = useState<any[] | null>(null); const [agents, setAgents] = useState<any[]>([]); const [nums, setNums] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [f, setF] = useState<any>({ name: "", agent_id: "", phone_number_id: "", concurrency: 2, raw: "", window: true, window_start: "09:30", window_end: "19:00", days: [0, 1, 2, 3, 4, 5], timezone: "", max_attempts: 2, retry_delay_min: 60, start_now: true });
  const [detail, setDetail] = useState<any>(null); const fi = useRef<HTMLInputElement>(null);
  const load = () => api("/api/campaigns").then(setList);
  useEffect(() => { load(); api("/api/agents").then(setAgents); api("/api/numbers").then(setNums); const t = setInterval(load, 6000); return () => clearInterval(t); }, []);
  const { contacts, columns } = parseCsv(f.raw);
  const agent = agents.find((a) => a.id === f.agent_id);
  const needed: string[] = (agent?.variables_used || []).filter((v: string) => !["customer_name", "name"].includes(v) && !(v in (agent?.variables || {})));
  const missing = needed.filter((v) => !columns.includes(v));

  const create = async () => {
    try {
      await api("/api/campaigns", { body: { name: f.name, agent_id: f.agent_id, phone_number_id: f.phone_number_id, concurrency: f.concurrency, contacts,
        window_start: f.window ? f.window_start : null, window_end: f.window ? f.window_end : null, window_days: f.window ? f.days.join(",") : null,
        timezone: f.timezone || null, max_attempts: f.max_attempts, retry_delay_min: f.retry_delay_min, start_now: f.start_now } });
      toast(f.start_now ? "Batch started" : "Batch created", "success"); setOpen(false); load();
    } catch (e: any) { toast(e.message, "error"); }
  };
  const act = async (id: string, a: string) => { try { await api(`/api/campaigns/${id}/${a}`, { method: "POST" }); load(); if (detail?.id === id) api(`/api/campaigns/${id}`).then(setDetail); } catch (e: any) { toast(e.message, "error"); } };
  if (!list) return <Loading />;

  return (
    <>
      <PageHeader title="Batch Calls" subtitle="Call a list of contacts automatically - with calling hours, retries and per-contact variables"
        actions={edit && <button className="btn primary" onClick={() => setOpen(true)}><Plus size={16} />New batch</button>} />
      <div className="card">
        {list.length ? (
          <div className="table-wrap"><table className="table"><thead><tr><th>Batch</th><th>Agent</th><th>From</th><th>Progress</th><th>Calling hours</th><th>Status</th><th></th></tr></thead>
            <tbody>{list.map((c) => {
              const pct = c.total ? ((c.completed + c.failed) / c.total) * 100 : 0;
              return (
                <tr key={c.id} className="click" onClick={() => api(`/api/campaigns/${c.id}`).then(setDetail)}>
                  <td><b>{c.name}</b><div className="small muted">{ago(c.created_at)}</div></td><td>{c.agent_name}</td><td className="mono">{c.from_number}</td>
                  <td style={{ minWidth: 190 }}><div className="progress"><div style={{ width: `${pct}%` }} /></div><div className="small muted" style={{ marginTop: 4 }}>{c.completed} reached · {c.failed} failed · {c.calling || 0} calling · {c.retrying || 0} retry · {c.total} total</div></td>
                  <td className="small">{c.window_start ? `${c.window_start}-${c.window_end}` : "Any time"}<div className="faint">{c.window_days ? c.window_days.split(",").map((d: string) => DAYS[Number(d)]).join(" ") : ""}</div></td>
                  <td><Badge live={c.status === "running"}>{c.status}</Badge></td>
                  <td onClick={(e) => e.stopPropagation()}>{edit && <div className="row" style={{ justifyContent: "flex-end" }}>
                    {c.status === "running" ? <button className="btn sm" onClick={() => act(c.id, "pause")}><Pause size={14} />Pause</button>
                      : !["completed", "stopped"].includes(c.status) && <button className="btn sm primary" onClick={() => act(c.id, "start")}><Play size={14} />{c.status === "draft" ? "Start" : "Resume"}</button>}
                    {!["completed", "stopped"].includes(c.status) && <button className="btn sm" title="Stop - skip all remaining contacts" onClick={() => confirm("Stop this batch? Remaining contacts are skipped.") && act(c.id, "stop")}><Square size={13} />Stop</button>}
                    {c.failed > 0 && <button className="btn sm" title="Call failed contacts again" onClick={() => act(c.id, "retry-failed")}><RotateCcw size={13} /></button>}
                    <button className="btn sm icon danger" onClick={async () => { if (confirm("Delete batch?")) { await api(`/api/campaigns/${c.id}`, { method: "DELETE" }); load(); } }}><Trash2 size={14} /></button></div>}</td>
                </tr>);
            })}</tbody></table></div>
        ) : <Empty icon={<Megaphone size={26} />} title="No batch calls yet" text="Upload a CSV of contacts and let your agent call them." action={edit && <button className="btn primary" onClick={() => setOpen(true)}><Plus size={16} />New batch</button>} />}
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title="New batch call" wide
        footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button><button className="btn primary" disabled={!f.name || !f.agent_id || !f.phone_number_id || !contacts.length} onClick={create}>{f.start_now ? "Create & start" : "Create"} ({contacts.length})</button></>}>
        <Field label="Batch name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="March payment reminders" /></Field>
        <div className="grid g3">
          <Field label="Agent"><select className="input" value={f.agent_id} onChange={(e) => setF({ ...f, agent_id: e.target.value })}><option value="">Select</option>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
          <Field label="Call from"><select className="input" value={f.phone_number_id} onChange={(e) => setF({ ...f, phone_number_id: e.target.value })}><option value="">Select</option>{nums.map((n) => <option key={n.id} value={n.id}>{n.number} ({n.provider})</option>)}</select></Field>
          <Field label="Parallel calls"><select className="input" value={f.concurrency} onChange={(e) => setF({ ...f, concurrency: Number(e.target.value) })}>{[1, 2, 3, 5, 10, 20].map((n) => <option key={n}>{n}</option>)}</select></Field>
        </div>
        <Field label="Contacts" hint="CSV with a header row: phone,name,due_amount,... - every extra column becomes a {{variable}} for that contact. Or one 'phone,name' per line.">
          <textarea className="input mono" style={{ minHeight: 130 }} value={f.raw} onChange={(e) => setF({ ...f, raw: e.target.value })} placeholder={"phone,name,due_amount,due_date\n+919876543210,Ravi,4500,15 March\n+971501234567,Aisha,1200,20 March"} />
        </Field>
        <div className="row wrap"><button className="btn sm" onClick={() => fi.current?.click()}><Upload size={14} />Upload CSV</button><span className="small muted">{contacts.length} valid contacts</span>
          {columns.length > 0 && <span className="small muted">Variables: {columns.map((c) => <span key={c} className="chip" style={{ marginRight: 4 }}>{`{{${c}}}`}</span>)}</span>}
          <input ref={fi} type="file" accept=".csv,.txt" hidden onChange={async (e) => { const file = e.target.files?.[0]; if (file) setF({ ...f, raw: await file.text() }); }} /></div>
        {f.agent_id && missing.length > 0 && <div className="small" style={{ color: "var(--warn)" }}>The agent uses {missing.map((m) => `{{${m}}}`).join(", ")} but your CSV has no such column - it will be empty.</div>}
        <div className="setting"><div className="txt"><b>Calling hours</b><span>Only dial inside this window (contact's local business hours)</span></div><Toggle on={f.window} onChange={(v) => setF({ ...f, window: v })} /></div>
        {f.window && <div className="sub stack" style={{ gap: 10 }}>
          <div className="grid g3">
            <Field label="From"><input className="input" type="time" value={f.window_start} onChange={(e) => setF({ ...f, window_start: e.target.value })} /></Field>
            <Field label="To"><input className="input" type="time" value={f.window_end} onChange={(e) => setF({ ...f, window_end: e.target.value })} /></Field>
            <Field label="Timezone" hint="Empty = your organisation's"><input className="input" placeholder="Asia/Dubai" value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} /></Field>
          </div>
          <div className="seg">{DAYS.map((d, i) => <button key={d} className={f.days.includes(i) ? "on" : ""} onClick={() => setF({ ...f, days: f.days.includes(i) ? f.days.filter((x: number) => x !== i) : [...f.days, i].sort() })}>{d}</button>)}</div>
        </div>}
        <div className="grid g3">
          <Field label="Attempts per contact" hint="Retries no-answer, busy and voicemail"><select className="input" value={f.max_attempts} onChange={(e) => setF({ ...f, max_attempts: Number(e.target.value) })}>{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}</select></Field>
          <Field label="Wait between attempts (min)"><input className="input" type="number" min={5} max={1440} value={f.retry_delay_min} onChange={(e) => setF({ ...f, retry_delay_min: Number(e.target.value) })} /></Field>
          <Field label="Start immediately"><div style={{ paddingTop: 6 }}><Toggle on={f.start_now} onChange={(v) => setF({ ...f, start_now: v })} /></div></Field>
        </div>
      </Modal>

      <Modal open={!!detail} onClose={() => setDetail(null)} title={<div className="row">{detail?.name}<Badge live={detail?.status === "running"}>{detail?.status}</Badge></div>} wide>
        <div className="table-wrap"><table className="table"><thead><tr><th>Phone</th><th>Name</th><th>Status</th><th>Attempts</th><th>Result</th><th>Duration</th><th>Outcome</th></tr></thead>
          <tbody>{detail?.contacts?.map((c: any) => <tr key={c.id}><td className="mono">{c.phone}</td><td>{c.name || "-"}{Object.keys(c.variables || {}).length > 0 && <div className="small faint">{Object.entries(c.variables).slice(0, 3).map(([k, v]) => `${k}: ${v}`).join(" · ")}</div>}</td>
            <td><Badge live={c.status === "calling"}>{c.status}</Badge></td><td>{c.attempts || 0}</td>
            <td className="small">{c.in_voicemail ? "Voicemail" : c.ended_reason ? reason(c.ended_reason) : c.last_error ? reason(c.last_error) : "-"}</td>
            <td>{fmtDur(c.duration_sec)}</td><td className="small">{c.outcome || "-"}</td></tr>)}</tbody></table></div>
      </Modal>
    </>
  );
}
