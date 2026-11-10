import { useEffect, useState } from "react";
import { KeyRound, Plus, Send, Trash2, Webhook } from "lucide-react";
import { api, API_BASE, ago } from "../api";
import { Badge, CopyBox, Empty, Field, Modal, PageHeader, useToast } from "../components/ui";

export default function Developers() {
  const toast = useToast();
  const [keys, setKeys] = useState<any[]>([]); const [hooks, setHooks] = useState<any[]>([]);
  const [newKey, setNewKey] = useState(""); const [kOpen, setKOpen] = useState(false); const [kName, setKName] = useState("");
  const [hOpen, setHOpen] = useState(false); const [hUrl, setHUrl] = useState(""); const [hEv, setHEv] = useState<string[]>(["call.ended", "call.analyzed"]);
  const load = () => { api("/api/keys").then(setKeys); api("/api/webhooks").then(setHooks); };
  useEffect(() => { load(); }, []);

  return (
    <div className="stack">
      <PageHeader title="Developers" subtitle="API keys and webhooks to integrate UBTI Voice Agent with your CRM and systems" />
      <div className="card">
        <div className="card-head"><h3><KeyRound size={16} style={{ verticalAlign: -3 }} /> API keys</h3><button className="btn sm primary" onClick={() => setKOpen(true)}><Plus size={14} />Create key</button></div>
        {keys.length ? <div className="table-wrap"><table className="table"><thead><tr><th>Name</th><th>Key</th><th>Created</th><th>Last used</th><th></th></tr></thead>
          <tbody>{keys.map((k) => <tr key={k.id}><td><b>{k.name}</b></td><td className="mono">{k.key_prefix}••••••••</td><td className="muted">{ago(k.created_at)}</td><td className="muted">{ago(k.last_used_at)}</td>
            <td><button className="btn sm danger" onClick={async () => { if (confirm("Revoke key?")) { await api(`/api/keys/${k.id}`, { method: "DELETE" }); load(); } }}>Revoke</button></td></tr>)}</tbody></table></div>
          : <Empty icon={<KeyRound size={26} />} title="No API keys" text="Use keys to trigger outbound calls from your CRM." />}
        <div className="card-pad" style={{ borderTop: "1px solid var(--border)" }}>
          <b className="small">Example - trigger an outbound call</b>
          <pre className="code" style={{ display: "block", whiteSpace: "pre-wrap", marginTop: 8 }}>{`curl -X POST ${API_BASE}/api/calls/outbound \\
  -H "X-API-Key: ubti_xxx" -H "Content-Type: application/json" \\
  -d '{"agent_id":"<agent-id>","phone_number_id":"<number-id>","to":"+919876543210",
       "variables":{"customer_name":"Ravi","due_amount":"4500"},"metadata":{"crm_id":"L-1029"}}'`}</pre>
          <div className="small muted" style={{ marginTop: 8 }}>Variables fill {"{{placeholders}}"} in the agent's prompt and first message. Metadata is stored with the call and returned in webhooks.</div>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h3><Webhook size={16} style={{ verticalAlign: -3 }} /> Webhooks</h3><button className="btn sm primary" onClick={() => setHOpen(true)}><Plus size={14} />Add endpoint</button></div>
        {hooks.length ? <div className="table-wrap"><table className="table"><thead><tr><th>URL</th><th>Events</th><th>Signing secret</th><th>Last delivery</th><th></th></tr></thead>
          <tbody>{hooks.map((h) => <tr key={h.id}><td className="mono" style={{ wordBreak: "break-all" }}>{h.url}</td><td><div className="row wrap" style={{ gap: 4 }}>{String(h.events).split(",").map((e: string) => <Badge key={e}>{e}</Badge>)}</div></td><td className="mono small">{h.secret.slice(0, 12)}…</td>
            <td>{h.last_status ? <Badge>{h.last_status}</Badge> : <span className="faint">never</span>}{h.last_error && <div className="small" style={{ color: "var(--danger)" }}>{h.last_error}</div>}</td>
            <td><div className="row" style={{ justifyContent: "flex-end" }}><button className="btn sm" onClick={async () => { try { const r = await api(`/api/webhooks/${h.id}/test`, { method: "POST" }); toast(`Endpoint responded ${r.status}`, "success"); } catch (e: any) { toast(e.message, "error"); } }}><Send size={13} />Test</button>
              <button className="btn sm icon danger" onClick={async () => { await api(`/api/webhooks/${h.id}`, { method: "DELETE" }); load(); }}><Trash2 size={13} /></button></div></td></tr>)}</tbody></table></div>
          : <Empty icon={<Webhook size={26} />} title="No webhooks" text="Receive call.started, call.ended and call.analyzed events (signed with HMAC-SHA256 of the body in X-UBTI-Signature, event name in X-UBTI-Event)." />}
        <div className="card-pad small muted" style={{ borderTop: "1px solid var(--border)" }}><b>call.started</b> when the AI picks up · <b>call.ended</b> right after hang-up with the transcript · <b>call.analyzed</b> when summary, sentiment and your custom post-call fields are ready. Failed deliveries are retried 3 times.</div>
      </div>

      <Modal open={kOpen} onClose={() => { setKOpen(false); setNewKey(""); setKName(""); }} title="Create API key"
        footer={!newKey && <button className="btn primary" disabled={!kName} onClick={async () => { const r = await api("/api/keys", { body: { name: kName } }); setNewKey(r.key); load(); }}>Create</button>}>
        {newKey ? <><div className="small" style={{ color: "var(--warn)" }}>Copy this key now - it won't be shown again.</div><CopyBox text={newKey} /></>
          : <Field label="Key name"><input className="input" value={kName} onChange={(e) => setKName(e.target.value)} placeholder="Salesforce integration" /></Field>}
      </Modal>
      <Modal open={hOpen} onClose={() => setHOpen(false)} title="Add webhook endpoint"
        footer={<button className="btn primary" disabled={!hUrl || !hEv.length} onClick={async () => { try { await api("/api/webhooks", { body: { url: hUrl, events: hEv.join(",") } }); setHOpen(false); setHUrl(""); load(); } catch (e: any) { toast(e.message, "error"); } }}>Add</button>}>
        <Field label="Endpoint URL" hint="We POST a JSON payload for each selected event"><input className="input" value={hUrl} onChange={(e) => setHUrl(e.target.value)} placeholder="https://your-crm.com/hooks/ubti" /></Field>
        <Field label="Events">{["call.started", "call.ended", "call.analyzed"].map((ev) => <label key={ev} className="row small" style={{ gap: 6 }}><input type="checkbox" checked={hEv.includes(ev)} onChange={(e) => setHEv(e.target.checked ? [...hEv, ev] : hEv.filter((x) => x !== ev))} /><span className="mono">{ev}</span></label>)}</Field>
      </Modal>
    </div>
  );
}
