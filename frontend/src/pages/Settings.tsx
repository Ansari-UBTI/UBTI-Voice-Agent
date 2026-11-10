import { useEffect, useState } from "react";
import { Building2, Lock, ScrollText, Trash2, UserPlus, Users, UserCog } from "lucide-react";
import { api, ago, fmtDate } from "../api";
import { can as canRole, useAuth } from "../auth";
import { Badge, Field, Modal, PageHeader, Tabs, Toggle, useToast } from "../components/ui";

export default function SettingsPage() {
  const { user, refresh } = useAuth(); const toast = useToast();
  const [tab, setTab] = useState("org"); const [org, setOrg] = useState<any>({}); const [team, setTeam] = useState<any[]>([]); const [audit, setAudit] = useState<any[]>([]);
  const [prof, setProf] = useState({ name: user?.name || "", password: "" });
  const [inv, setInv] = useState<any>(null);
  const load = () => { api("/api/settings/org").then(setOrg); api("/api/settings/team").then(setTeam); if (canRole(user, "admin")) api("/api/settings/audit").then(setAudit).catch(() => {}); };
  useEffect(() => { load(); }, []);
  const can = canRole(user, "admin");

  return (
    <div className="stack">
      <PageHeader title="Settings" subtitle="Organisation, team members and your profile" />
      <div className="card">
        <div style={{ padding: "0 12px" }}><Tabs value={tab} onChange={setTab} tabs={[{ id: "org", label: "Organisation", icon: <Building2 size={15} /> }, { id: "team", label: "Team", icon: <Users size={15} /> }, { id: "privacy", label: "Data & privacy", icon: <Lock size={15} /> }, { id: "profile", label: "Profile", icon: <UserCog size={15} /> }, ...(can ? [{ id: "audit", label: "Activity log", icon: <ScrollText size={15} /> }] : [])]} /></div>
        <div className="card-pad stack">
          {tab === "org" && <>
            <div className="grid g2">
              <Field label="Organisation name"><input className="input" disabled={!can} value={org.name || ""} onChange={(e) => setOrg({ ...org, name: e.target.value })} /></Field>
              <Field label="Country / data region"><select className="input" disabled={!can} value={org.country || "IN"} onChange={(e) => setOrg({ ...org, country: e.target.value })}><option value="IN">India</option><option value="AE">UAE</option><option value="US">United States</option><option value="SG">Singapore</option></select></Field>
              <Field label="Timezone"><input className="input" disabled={!can} value={org.timezone || ""} placeholder="Asia/Kolkata" onChange={(e) => setOrg({ ...org, timezone: e.target.value })} /></Field>
              <Field label="Plan"><div className="row" style={{ paddingTop: 8 }}><Badge>{org.plan}</Badge><span className="muted small">{org.minutes_quota} minutes / month</span></div></Field>
            </div>
            {can && <div><button className="btn primary" onClick={async () => { await api("/api/settings/org", { method: "PUT", body: { name: org.name, country: org.country, timezone: org.timezone } }); toast("Saved", "success"); refresh(); }}>Save</button></div>}
          </>}
          {tab === "team" && <>
            <div className="row between"><span className="muted">{team.length} members</span>{can && <button className="btn sm primary" onClick={() => setInv({ name: "", email: "", password: "", role: "member" })}><UserPlus size={14} />Add member</button>}</div>
            <div className="table-wrap"><table className="table"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Last login</th><th></th></tr></thead>
              <tbody>{team.map((m) => <tr key={m.id}><td><b>{m.name}</b></td><td>{m.email}</td><td>{can && m.role !== "owner" && m.id !== user?.id ? <select className="input sm" style={{ width: 120 }} value={m.role} onChange={async (e) => { try { await api(`/api/settings/team/${m.id}`, { method: "PUT", body: { role: e.target.value } }); toast("Role updated", "success"); load(); } catch (er: any) { toast(er.message, "error"); } }}>{["admin", "member", "viewer"].map((r) => <option key={r}>{r}</option>)}</select> : <Badge tone={m.role === "owner" ? "violet" : ""}>{m.role}</Badge>}</td><td className="muted">{ago(m.last_login_at)}</td>
                <td>{can && m.role !== "owner" && m.id !== user?.id && <button className="btn icon sm danger" onClick={async () => { await api(`/api/settings/team/${m.id}`, { method: "DELETE" }); load(); }}><Trash2 size={14} /></button>}</td></tr>)}</tbody></table></div>
          </>}
          {tab === "privacy" && <>
            <div className="setting"><div className="txt"><b>Redact personal data</b><span>After each call, phone numbers, emails, card numbers, Emirates ID / Aadhaar / PAN and IBANs are masked in stored transcripts, summaries and webhooks. Recordings are not altered - turn recording off per agent if needed.</span></div>
              <Toggle on={!!org.pii_redaction} onChange={async (v) => { if (!can) return; setOrg(await api("/api/settings/privacy", { method: "PUT", body: { pii_redaction: v } })); toast(v ? "Redaction on" : "Redaction off", "success"); }} /></div>
            <div className="setting"><div className="txt"><b>Keep call data for</b><span>Calls, transcripts and recordings older than this are deleted automatically (checked hourly).</span></div>
              <select className="input" disabled={!can} style={{ width: 170 }} value={org.retention_days || 0} onChange={async (e) => { setOrg(await api("/api/settings/privacy", { method: "PUT", body: { retention_days: Number(e.target.value) } })); toast("Retention updated", "success"); }}>
                {[[0, "Forever"], [7, "7 days"], [30, "30 days"], [90, "90 days"], [180, "6 months"], [365, "1 year"], [730, "2 years"]].map(([d, l]) => <option key={d} value={d}>{l}</option>)}</select></div>
            {!can && <div className="small muted">Only owners and admins can change these settings.</div>}
          </>}
          {tab === "profile" && <div className="grid g2">
            <Field label="Name"><input className="input" value={prof.name} onChange={(e) => setProf({ ...prof, name: e.target.value })} /></Field>
            <Field label="Email"><input className="input" disabled value={user?.email} /></Field>
            <Field label="New password" hint="Leave empty to keep current"><input className="input" type="password" value={prof.password} onChange={(e) => setProf({ ...prof, password: e.target.value })} /></Field>
            <div style={{ alignSelf: "end" }}><button className="btn primary" onClick={async () => { try { await api("/api/settings/profile", { method: "PUT", body: { name: prof.name, password: prof.password || null } }); toast("Profile updated", "success"); refresh(); setProf({ ...prof, password: "" }); } catch (e: any) { toast(e.message, "error"); } }}>Update profile</button></div>
          </div>}
          {tab === "audit" && <div className="table-wrap"><table className="table"><thead><tr><th>When</th><th>User</th><th>Action</th><th>Detail</th></tr></thead>
            <tbody>{audit.map((a, i) => <tr key={i}><td className="muted">{fmtDate(a.created_at)}</td><td>{a.user_name || "-"}</td><td><Badge tone="gray">{a.action}</Badge></td><td className="small">{a.detail}</td></tr>)}</tbody></table></div>}
        </div>
      </div>
      <Modal open={!!inv} onClose={() => setInv(null)} title="Add team member"
        footer={<button className="btn primary" disabled={!inv?.email || !inv?.name || (inv?.password || "").length < 8} onClick={async () => { try { await api("/api/settings/team", { body: inv }); toast("Member added", "success"); setInv(null); load(); } catch (e: any) { toast(e.message, "error"); } }}>Add member</button>}>
        <Field label="Name"><input className="input" value={inv?.name || ""} onChange={(e) => setInv({ ...inv, name: e.target.value })} /></Field>
        <Field label="Email"><input className="input" value={inv?.email || ""} onChange={(e) => setInv({ ...inv, email: e.target.value })} /></Field>
        <div className="grid g2">
          <Field label="Temporary password" hint="Min 8 characters"><input className="input" type="password" value={inv?.password || ""} onChange={(e) => setInv({ ...inv, password: e.target.value })} /></Field>
          <Field label="Role"><select className="input" value={inv?.role} onChange={(e) => setInv({ ...inv, role: e.target.value })}><option value="admin">Admin</option><option value="member">Member</option><option value="viewer">Viewer</option></select></Field>
        </div>
      </Modal>
    </div>
  );
}
