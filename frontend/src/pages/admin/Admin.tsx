import { useEffect, useState } from "react";
import { Building2, CheckCircle2, Hash, Mic2, Phone, PhoneCall, Radio, Search, ShieldCheck, Users, XCircle } from "lucide-react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip, Legend } from "recharts";
import { api, ago, fmtDate, fmtDur } from "../../api";
import { Badge, Empty, Field, Loading, Modal, PageHeader, Stat, useToast } from "../../components/ui";
import { Charts, Kpis, PIE } from "../Analytics";
import { CallDetail } from "../Calls";

export function AdminOverview() {
  const [d, setD] = useState<any>(null); const [days, setDays] = useState(30);
  useEffect(() => { setD(null); api(`/api/admin/overview?days=${days}`).then(setD); }, [days]);
  if (!d) return <Loading />;
  const p = d.platform || {};
  return (
    <div className="stack">
      <PageHeader title="Platform Overview" subtitle="UBTI admin - all tenants, usage and health"
        actions={<div className="seg">{[7, 30, 90].map((n) => <button key={n} className={days === n ? "on" : ""} onClick={() => setDays(n)}>{n} days</button>)}</div>} />
      <div className="grid g4">
        <Stat icon={<Building2 size={19} />} label={`Tenants (${p.active_tenants} active)`} value={p.tenants} tone="violet" />
        <Stat icon={<Users size={19} />} label="Users" value={p.users} />
        <Stat icon={<Hash size={19} />} label="Numbers connected" value={p.numbers} tone="cyan" />
        <Stat icon={<Radio size={19} />} label="Live calls now" value={p.live_calls} tone="green" />
      </div>
      <Kpis k={d.kpis} />
      <div className="grid g-2-1">
        <div className="card card-pad row between">
          <div className="row"><div className="agent-ava" style={{ background: "linear-gradient(135deg,#d98a07,#e0475b)" }}><Mic2 size={19} /></div><div><b>{p.pending_voices} voice clone requests</b><div className="small muted">waiting for consent review</div></div></div>
          <a className="btn sm" href="/admin/voices">Review</a>
        </div>
        <div className="card"><div className="card-head"><h3>Tenants by plan</h3></div><div style={{ height: 180 }}><ResponsiveContainer><PieChart><Pie data={d.plans} dataKey="value" nameKey="name" innerRadius={40} outerRadius={70}>{d.plans?.map((_: any, i: number) => <Cell key={i} fill={PIE[i % 6]} />)}</Pie><Tooltip /><Legend /></PieChart></ResponsiveContainer></div></div>
      </div>
      <Charts d={d} />
    </div>
  );
}

export function AdminTenants() {
  const toast = useToast();
  const [list, setList] = useState<any[] | null>(null); const [q, setQ] = useState(""); const [edit, setEdit] = useState<any>(null);
  const load = () => api(`/api/admin/tenants?q=${encodeURIComponent(q)}`).then(setList);
  useEffect(() => { load(); }, []);
  const save = async () => { await api(`/api/admin/tenants/${edit.id}`, { method: "PUT", body: { plan: edit.plan, status: edit.status, minutes_quota: Number(edit.minutes_quota), max_concurrent_calls: Number(edit.max_concurrent_calls || 5) } }); toast("Tenant updated", "success"); setEdit(null); load(); };
  return (
    <>
      <PageHeader title="Tenants" subtitle="All client organisations on the platform"
        actions={<form className="search" onSubmit={(e) => { e.preventDefault(); load(); }}><Search size={15} /><input className="input" placeholder="Search tenants" value={q} onChange={(e) => setQ(e.target.value)} /></form>} />
      <div className="card">{!list ? <Loading /> : list.length ? (
        <div className="table-wrap"><table className="table"><thead><tr><th>Organisation</th><th>Country</th><th>Plan</th><th>Usage (month)</th><th>Agents</th><th>Numbers</th><th>Calls</th><th>Status</th><th></th></tr></thead>
          <tbody>{list.map((t) => {
            const pct = Math.min(100, (t.minutes_month / (t.minutes_quota || 1)) * 100);
            return (<tr key={t.id}>
              <td><b>{t.name}</b><div className="small muted">{t.owner_email} · joined {ago(t.created_at)}</div></td><td>{t.country}</td><td><Badge>{t.plan}</Badge></td>
              <td style={{ minWidth: 150 }}><div className="progress"><div style={{ width: `${pct}%`, background: pct > 90 ? "var(--danger)" : undefined }} /></div><div className="small muted" style={{ marginTop: 4 }}>{Math.round(t.minutes_month)} / {t.minutes_quota} min</div></td>
              <td>{t.agents}</td><td>{t.numbers}</td><td>{t.calls}</td><td><Badge>{t.status}</Badge></td>
              <td><div className="row" style={{ justifyContent: "flex-end" }}><button className="btn sm" onClick={() => setEdit({ ...t })}>Manage</button>
                <button className={`btn sm ${t.status === "active" ? "danger" : ""}`} onClick={async () => { await api(`/api/admin/tenants/${t.id}`, { method: "PUT", body: { status: t.status === "active" ? "suspended" : "active" } }); load(); }}>{t.status === "active" ? "Suspend" : "Activate"}</button></div></td>
            </tr>);
          })}</tbody></table></div>) : <Empty title="No tenants" />}</div>
      <Modal open={!!edit} onClose={() => setEdit(null)} title={`Manage ${edit?.name}`} footer={<button className="btn primary" onClick={save}>Save</button>}>
        <div className="grid g2">
          <Field label="Plan"><select className="input" value={edit?.plan} onChange={(e) => setEdit({ ...edit, plan: e.target.value, minutes_quota: { trial: 100, starter: 1000, growth: 5000, enterprise: 50000 }[e.target.value] })}>{["trial", "starter", "growth", "enterprise"].map((p) => <option key={p}>{p}</option>)}</select></Field>
          <Field label="Status"><select className="input" value={edit?.status} onChange={(e) => setEdit({ ...edit, status: e.target.value })}><option value="active">active</option><option value="suspended">suspended</option></select></Field>
        </div>
        <Field label="Monthly minutes quota"><input className="input" type="number" value={edit?.minutes_quota || 0} onChange={(e) => setEdit({ ...edit, minutes_quota: e.target.value })} /></Field>
        <Field label="Max parallel calls" hint="Inbound + outbound + batch calls at the same time"><input className="input" type="number" min={1} max={500} value={edit?.max_concurrent_calls || 5} onChange={(e) => setEdit({ ...edit, max_concurrent_calls: e.target.value })} /></Field>
      </Modal>
    </>
  );
}

export function AdminCalls() {
  const [d, setD] = useState<any>(null); const [page, setPage] = useState(1); const [open, setOpen] = useState<any>(null);
  useEffect(() => { api(`/api/admin/calls?page=${page}&size=25`).then(setD); }, [page]);
  const pages = d ? Math.max(1, Math.ceil(d.total / 25)) : 1;
  return (<>
    <PageHeader title="All Calls" subtitle="Every call across all tenants" />
    <div className="card">{!d ? <Loading /> : d.items.length ? (<>
      <div className="table-wrap"><table className="table"><thead><tr><th>Date</th><th>Tenant</th><th>Agent</th><th>Provider</th><th>Direction</th><th>Number</th><th>Duration</th><th>Status</th><th>Cost</th></tr></thead>
        <tbody>{d.items.map((c: any) => <tr key={c.id} className="click" onClick={() => api(`/api/admin/calls/${c.id}`).then(setOpen)}>
          <td className="muted">{fmtDate(c.started_at)}</td><td><b>{c.tenant_name}</b></td><td>{c.agent_name}</td><td><Badge tone="gray">{c.provider}</Badge></td><td><Badge>{c.direction}</Badge></td>
          <td className="mono">{c.direction === "inbound" ? c.from_number : c.to_number}</td><td>{fmtDur(c.duration_sec)}</td><td><Badge live={c.status === "in-progress"}>{c.status}</Badge></td><td>${(c.cost_usd || 0).toFixed(3)}</td></tr>)}</tbody></table></div>
      <div className="row between" style={{ padding: 14 }}><span className="small muted">{d.total} calls</span><div className="row"><button className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</button><span className="small">{page}/{pages}</span><button className="btn sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button></div></div>
    </>) : <Empty icon={<PhoneCall size={26} />} title="No calls yet" />}</div>
    <CallDetail call={open} onClose={() => setOpen(null)} />
  </>);
}

export function AdminNumbers() {
  const [list, setList] = useState<any[] | null>(null);
  useEffect(() => { api("/api/admin/numbers").then(setList); }, []);
  return (<>
    <PageHeader title="All Numbers" subtitle="Phone number inventory across tenants" />
    <div className="card">{!list ? <Loading /> : list.length ? <div className="table-wrap"><table className="table"><thead><tr><th>Number</th><th>Tenant</th><th>Provider</th><th>Country</th><th>Agent</th><th>Status</th><th>Added</th></tr></thead>
      <tbody>{list.map((n) => <tr key={n.id}><td className="mono"><b>{n.number}</b></td><td>{n.tenant_name}</td><td><Badge tone="gray">{n.provider}</Badge></td><td>{n.country}</td><td>{n.agent_name || "-"}</td><td><Badge>{n.status}</Badge></td><td className="muted">{ago(n.created_at)}</td></tr>)}</tbody></table></div>
      : <Empty icon={<Phone size={26} />} title="No numbers" />}</div>
  </>);
}

export function AdminVoices() {
  const toast = useToast(); const [list, setList] = useState<any[] | null>(null);
  const load = () => api("/api/admin/voices").then(setList);
  useEffect(() => { load(); const t = setInterval(load, 6000); return () => clearInterval(t); }, []);
  const review = async (id: string, approve: boolean) => {
    const note = approve ? "" : prompt("Reason for rejection?") || "";
    await api(`/api/admin/voices/${id}/review`, { body: { approve, note } }); toast(approve ? "Approved - creating voice in Azure" : "Rejected", "success"); load();
  };
  return (<>
    <PageHeader title="Voice Approvals" subtitle="Review consent before any voice is cloned (Responsible AI)" />
    <div className="card">{!list ? <Loading /> : list.length ? <div className="table-wrap"><table className="table"><thead><tr><th>Voice</th><th>Tenant</th><th>Talent</th><th>Locale</th><th>Status</th><th>Requested</th><th></th></tr></thead>
      <tbody>{list.map((v) => <tr key={v.id}><td><b>{v.name}</b>{v.error && <div className="small" style={{ color: "var(--danger)" }}>{v.error.slice(0, 100)}</div>}{v.review_note && <div className="small muted">{v.review_note}</div>}</td>
        <td>{v.tenant_name}</td><td>{v.talent_name}<div className="small muted">{v.company_name}</div></td><td>{v.locale}</td><td><Badge live={v.status === "processing"}>{v.status}</Badge></td><td className="muted">{ago(v.created_at)}</td>
        <td>{v.status === "pending_review" || v.status === "failed" ? <div className="row" style={{ justifyContent: "flex-end" }}>
          <button className="btn sm primary" onClick={() => review(v.id, true)}><CheckCircle2 size={14} />{v.status === "failed" ? "Retry" : "Approve"}</button>
          <button className="btn sm danger" onClick={() => review(v.id, false)}><XCircle size={14} />Reject</button></div> : null}</td></tr>)}</tbody></table></div>
      : <Empty icon={<ShieldCheck size={26} />} title="No voice requests" />}</div>
  </>);
}

export function AdminAudit() {
  const [list, setList] = useState<any[]>([]); const [h, setH] = useState<any>(null);
  useEffect(() => { api("/api/admin/audit").then(setList); api("/api/admin/health").then(setH); }, []);
  const item = (k: string, ok: boolean) => <div key={k} className="card card-pad row between" style={{ padding: 14 }}><b>{k}</b>{ok ? <Badge tone="green"><CheckCircle2 size={12} />Connected</Badge> : <Badge tone="red"><XCircle size={12} />Not configured</Badge>}</div>;
  return (<div className="stack">
    <PageHeader title="Audit & Health" subtitle="Service connectivity and platform activity" />
    {h && <><div className="grid g3">{item("Fabric SQL Database", h.fabric_sql)}{item("Azure Voice Live", h.voice_live)}{item("Azure Speech (voices)", h.speech)}{item("Azure OpenAI (summaries)", h.azure_openai)}{item("Vobiz (platform)", h.vobiz)}{item("Twilio (platform)", h.twilio)}{item("Azure Communication Services", h.acs)}{item("Blob storage (recordings)", h.blob_storage)}{item("Redis (multi-replica)", h.redis)}{item("Knowledge embeddings", h.embeddings)}</div>
      <div className="small muted">Public URL: <span className="mono">{h.public_url}</span> · Default model: <b>{h.model}</b></div></>}
    <div className="card"><div className="card-head"><h3>Activity</h3></div><div className="table-wrap"><table className="table"><thead><tr><th>When</th><th>Tenant</th><th>User</th><th>Action</th><th>Detail</th></tr></thead>
      <tbody>{list.map((a, i) => <tr key={i}><td className="muted">{fmtDate(a.created_at)}</td><td>{a.tenant_name}</td><td>{a.email || "-"}</td><td><Badge tone="gray">{a.action}</Badge></td><td className="small" style={{ maxWidth: 320, wordBreak: "break-word" }}>{a.detail}</td></tr>)}</tbody></table></div></div>
  </div>);
}
