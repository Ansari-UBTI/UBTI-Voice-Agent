import { useEffect, useState } from "react";
import { Phone, PhoneOutgoing, Plus, Trash2, KeyRound } from "lucide-react";
import { api, ago } from "../api";
import { can, useAuth } from "../auth";
import { Badge, CopyBox, Empty, Field, Loading, Modal, PageHeader, useToast } from "../components/ui";

const P: Record<string, { name: string; color: string; sub: string; help: string[] }> = {
  vobiz: { name: "Vobiz", color: "#0ea5e9", sub: "Indian numbers · low cost", help: [
    "Vobiz console > Voice > Applications > Create application",
    "Paste the Answer URL below (method POST) and the Status URL as Hangup URL",
    "Phone Numbers > select your number > attach the application"] },
  twilio: { name: "Twilio", color: "#e0475b", sub: "US, UAE & global", help: [
    "Twilio Console > Phone Numbers > Active numbers > select number",
    "Voice Configuration > 'A call comes in' > Webhook > paste the Answer URL (HTTP POST)",
    "Paste the Status URL in 'Call status changes' and Save"] },
  acs: { name: "Azure (ACS)", color: "#2563eb", sub: "Azure numbers & SIP trunks (Direct Routing)", help: [
    "Azure portal > your Communication Services resource > Phone numbers: buy a number, or Direct routing: connect your SIP trunk / SBC and add a voice route",
    "Credentials (above): paste the resource's connection string (Keys > Connection string)",
    "Resource > Events > + Event Subscription: event type 'Incoming Call', endpoint type Web Hook, URL below",
    "Add the number here in +E.164 format (for Direct Routing: the number your trunk sends) and assign an agent"] },
};

export default function Numbers() {
  const toast = useToast(); const { user } = useAuth(); const adm = can(user, "admin"); const mem = can(user, "member");
  const [list, setList] = useState<any[] | null>(null); const [agents, setAgents] = useState<any[]>([]); const [prov, setProv] = useState<any[]>([]);
  const [open, setOpen] = useState(false); const [f, setF] = useState({ provider: "vobiz", number: "", label: "", agent_id: "", country: "IN" });
  const [created, setCreated] = useState<any>(null);
  const [cred, setCred] = useState<any>(null);
  const [call, setCall] = useState<any>(null);
  const load = () => { api("/api/numbers").then(setList); api("/api/numbers/providers").then(setProv); };
  useEffect(() => { load(); api("/api/agents").then(setAgents); }, []);

  const add = async () => {
    try { const r = await api("/api/numbers", { body: { ...f, agent_id: f.agent_id || null } }); setCreated(r); setOpen(false); load(); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const assign = async (id: string, agent_id: string) => { try { await api(`/api/numbers/${id}`, { method: "PUT", body: { agent_id } }); toast("Agent assigned", "success"); load(); } catch (e: any) { toast(e.message, "error"); } };
  const saveCred = async () => { try { await api("/api/numbers/providers", { method: "PUT", body: cred }); toast("Credentials saved securely", "success"); setCred(null); load(); } catch (e: any) { toast(e.message, "error"); } };
  const dial = async () => {
    let variables = {}; try { variables = call.vars ? JSON.parse(call.vars) : {}; } catch { toast("Variables must be JSON", "error"); return; }
    try { await api("/api/calls/outbound", { body: { phone_number_id: call.phone_number_id, agent_id: call.agent_id, to: call.to, variables } }); toast("Calling " + call.to, "success"); setCall(null); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const provInfo = (p: string) => prov.find((x) => x.provider === p);
  if (!list) return <Loading />;
  const wh = created?.webhooks || {};

  return (
    <>
      <PageHeader title="Phone Numbers" subtitle="Connect Vobiz, Twilio or Azure numbers (including your own SIP trunk) and assign an agent"
        actions={adm && <button className="btn primary" onClick={() => setOpen(true)}><Plus size={16} />Connect number</button>} />
      <div className="grid g3 mb">
        {prov.map((p) => (
          <div key={p.provider} className="card card-pad row between">
            <div className="row"><div className="agent-ava" style={{ background: P[p.provider].color }}><Phone size={19} /></div>
              <div><b>{P[p.provider].name}</b><div className="small muted" style={{ wordBreak: "break-all" }}>{p.connected ? `Account ${p.account_id}` : p.platform_default ? "Using UBTI platform account" : "Not connected"}</div></div></div>
            <div className="row"><Badge tone={p.connected || p.platform_default ? "green" : "gray"}>{p.connected ? "Connected" : p.platform_default ? "Default" : "Not set"}</Badge>
              {adm && <button className="btn sm icon" title="Credentials" onClick={() => setCred({ provider: p.provider, account_id: p.account_id || "", auth_token: "" })}><KeyRound size={14} /></button>}</div>
          </div>))}
      </div>
      <div className="card">
        {list.length ? (
          <div className="table-wrap"><table className="table"><thead><tr><th>Number</th><th>Provider</th><th>Assigned agent</th><th>Status</th><th>Added</th><th></th></tr></thead>
            <tbody>{list.map((n) => (
              <tr key={n.id}>
                <td><b className="mono" style={{ fontSize: 14 }}>{n.number}</b><div className="small muted">{n.label}</div></td>
                <td><Badge tone={n.provider === "twilio" ? "red" : n.provider === "acs" ? "violet" : ""}>{P[n.provider]?.name || n.provider}</Badge></td>
                <td><select className="input" disabled={!mem} style={{ minWidth: 180, padding: "7px 10px" }} value={n.agent_id || ""} onChange={(e) => assign(n.id, e.target.value)}>
                  <option value="">- No agent -</option>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></td>
                <td><Badge>{n.status}</Badge></td><td className="muted">{ago(n.created_at)}</td>
                <td><div className="row" style={{ justifyContent: "flex-end" }}>
                  {adm && <button className="btn sm" onClick={() => setCreated(n)}>Setup</button>}
                  {mem && <button className="btn sm" disabled={!n.agent_id} onClick={() => setCall({ phone_number_id: n.id, agent_id: n.agent_id, to: "", vars: "" })}><PhoneOutgoing size={14} />Call</button>}
                  {adm && <button className="btn sm icon danger" onClick={async () => { if (confirm("Disconnect number?")) { await api(`/api/numbers/${n.id}`, { method: "DELETE" }); load(); } }}><Trash2 size={14} /></button>}</div></td>
              </tr>))}</tbody></table></div>
        ) : <Empty icon={<Phone size={26} />} title="No numbers connected" text="Connect a Vobiz (India), Twilio (US/UAE) or Azure Communication Services number." action={adm && <button className="btn primary" onClick={() => setOpen(true)}><Plus size={16} />Connect number</button>} />}
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title="Connect a phone number"
        footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button><button className="btn primary" disabled={!f.number} onClick={add}>Connect</button></>}>
        <div className="grid g3">{Object.entries(P).map(([k, p]) => (
          <div key={k} className={`tpl ${f.provider === k ? "on" : ""}`} onClick={() => setF({ ...f, provider: k, country: k === "vobiz" ? "IN" : f.country })}>
            <b>{p.name}</b><div className="small muted">{p.sub}</div></div>))}</div>
        {f.provider === "acs" && !provInfo("acs")?.connected && !provInfo("acs")?.platform_default && <div className="small" style={{ color: "var(--warn)" }}>Add your ACS connection string first (key icon on the Azure card).</div>}
        <Field label="Phone number" hint="International format with country code"><input className="input" placeholder="+971 4 123 4567" value={f.number} onChange={(e) => setF({ ...f, number: e.target.value })} /></Field>
        <div className="grid g2">
          <Field label="Label"><input className="input" placeholder="Sales hotline" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></Field>
          <Field label="Country"><select className="input" value={f.country} onChange={(e) => setF({ ...f, country: e.target.value })}><option value="IN">India</option><option value="AE">UAE</option><option value="US">US</option><option value="SG">Singapore</option><option value="GB">UK</option></select></Field>
        </div>
        <Field label="Answering agent"><select className="input" value={f.agent_id} onChange={(e) => setF({ ...f, agent_id: e.target.value })}><option value="">Select later</option>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
      </Modal>

      <Modal open={!!created} onClose={() => setCreated(null)} title={`Finish setup in ${P[created?.provider || "vobiz"].name}`}>
        <ol style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>{P[created?.provider || "vobiz"].help.map((h) => <li key={h}>{h}</li>)}</ol>
        {created?.provider === "acs" ? <>
          <Field label="Event Grid webhook URL (Incoming Call)"><CopyBox text={wh.event_grid_url || ""} /></Field>
          <div className="small muted">Call events, transfers and media streaming are configured automatically for each call. Keep this URL private - the key in it identifies your account.</div>
        </> : <>
          <Field label="Answer URL (POST)"><CopyBox text={wh.answer_url || ""} /></Field>
          <Field label="Status / Hangup URL (POST)"><CopyBox text={wh.status_url || ""} /></Field>
          <div className="small muted">These URLs contain your account key - keep them private. Numbers set up with the old URLs (no ?k=) must be updated.</div>
        </>}
      </Modal>

      <Modal open={!!cred} onClose={() => setCred(null)} title={`${P[cred?.provider || "vobiz"].name} credentials`}
        footer={<><button className="btn" onClick={() => setCred(null)}>Cancel</button><button className="btn primary" disabled={!cred?.auth_token || (cred?.provider !== "acs" && !cred?.account_id)} onClick={saveCred}>Save</button></>}>
        {cred?.provider === "acs" ? <>
          <Field label="Connection string" hint="Azure portal > Communication Services > Keys. Encrypted before saving."><textarea className="input mono" style={{ minHeight: 80 }} placeholder="endpoint=https://xxx.communication.azure.com/;accesskey=..." value={cred?.auth_token || ""} onChange={(e) => setCred({ ...cred, auth_token: e.target.value })} /></Field>
        </> : <>
          <Field label={cred?.provider === "twilio" ? "Account SID" : "Auth ID"}><input className="input" value={cred?.account_id || ""} onChange={(e) => setCred({ ...cred, account_id: e.target.value })} /></Field>
          <Field label="Auth token" hint="Encrypted before saving. Needed for outbound calls, transfers and batch calls."><input className="input" type="password" value={cred?.auth_token || ""} onChange={(e) => setCred({ ...cred, auth_token: e.target.value })} /></Field>
        </>}
      </Modal>

      <Modal open={!!call} onClose={() => setCall(null)} title="Make an outbound call"
        footer={<><button className="btn" onClick={() => setCall(null)}>Cancel</button><button className="btn primary" disabled={!call?.to} onClick={dial}><PhoneOutgoing size={15} />Call now</button></>}>
        <Field label="Agent"><select className="input" value={call?.agent_id || ""} onChange={(e) => setCall({ ...call, agent_id: e.target.value })}>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
        <Field label="Number to call"><input className="input" placeholder="+9198XXXXXXXX" value={call?.to || ""} onChange={(e) => setCall({ ...call, to: e.target.value })} /></Field>
        <Field label="Variables (optional JSON)" hint='Fills {{placeholders}} in the prompt, e.g. {"customer_name": "Ravi", "due_amount": "4500"}'>
          <textarea className="input mono" style={{ minHeight: 70 }} value={call?.vars || ""} onChange={(e) => setCall({ ...call, vars: e.target.value })}
            placeholder={JSON.stringify(Object.fromEntries((agents.find((a) => a.id === call?.agent_id)?.variables_used || []).map((v: string) => [v, ""])))} /></Field>
      </Modal>
    </>
  );
}
