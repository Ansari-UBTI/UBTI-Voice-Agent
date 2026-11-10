import { useEffect, useState } from "react";
import { FlaskConical, Pencil, Plus, Trash2, Wrench, X } from "lucide-react";
import { api } from "../../api";
import { Badge, Empty, Field, Modal, Toggle, useToast } from "../ui";

type Param = { name: string; type: string; description: string; required: boolean };
type Tool = {
  id?: string; name: string; description: string; method: string; url: string; headers: Record<string, string>;
  parameters: Param[]; wait_message?: string; timeout_sec: number; retries: number; enabled: boolean;
};
const blank: Tool = { name: "", description: "", method: "POST", url: "", headers: {}, parameters: [], wait_message: "", timeout_sec: 8, retries: 1, enabled: true };

export default function FunctionsTab({ agentId, onChanged, readOnly }: { agentId: string; onChanged: () => void; readOnly?: boolean }) {
  const toast = useToast();
  const [list, setList] = useState<Tool[]>([]);
  const [edit, setEdit] = useState<Tool | null>(null);
  const [hdrs, setHdrs] = useState<[string, string][]>([]);
  const [test, setTest] = useState<{ tool: Tool; args: string; result?: string; busy?: boolean } | null>(null);
  const load = () => api(`/api/agents/${agentId}/tools`).then(setList);
  useEffect(() => { load(); }, [agentId]);

  const open = (t: Tool) => { setEdit({ ...t, parameters: t.parameters.map((p) => ({ ...p })) }); setHdrs(Object.entries(t.headers || {})); };
  const save = async () => {
    if (!edit) return;
    const body = { ...edit, headers: Object.fromEntries(hdrs.filter(([k]) => k.trim())) };
    try {
      if (edit.id) await api(`/api/agents/${agentId}/tools/${edit.id}`, { method: "PUT", body });
      else await api(`/api/agents/${agentId}/tools`, { body });
      toast("Function saved to draft - publish to use it on live calls", "success"); setEdit(null); load(); onChanged();
    } catch (e: any) { toast(e.message, "error"); }
  };
  const del = async (t: Tool) => { if (!confirm(`Delete ${t.name}?`)) return; await api(`/api/agents/${agentId}/tools/${t.id}`, { method: "DELETE" }); load(); onChanged(); };
  const runTest = async () => {
    if (!test) return;
    let args = {}; try { args = JSON.parse(test.args || "{}"); } catch { toast("Arguments must be JSON", "error"); return; }
    setTest({ ...test, busy: true });
    try { const r = await api(`/api/agents/${agentId}/tools/${test.tool.id}/test`, { body: { args } }); setTest({ ...test, result: r.result, busy: false }); }
    catch (e: any) { setTest({ ...test, result: e.message, busy: false }); }
  };
  const setP = (i: number, k: keyof Param, v: any) => setEdit({ ...edit!, parameters: edit!.parameters.map((p, j) => (j === i ? { ...p, [k]: v } : p)) });

  return (
    <>
      <div className="row between wrap">
        <span className="muted">Let the agent call your APIs during the call - check an order, book a slot, create a lead. Use <span className="chip">{"{{variable}}"}</span> in the URL or headers.</span>
        {!readOnly && <button className="btn sm primary" onClick={() => open(blank)}><Plus size={14} />Add function</button>}
      </div>
      {list.length ? list.map((t) => (
        <div key={t.id} className="setting">
          <div className="txt"><div className="row" style={{ gap: 8 }}><b className="mono">{t.name}</b><Badge tone="gray">{t.method}</Badge>{!t.enabled && <Badge tone="amber">disabled</Badge>}{t.retries > 0 && <Badge tone="gray">{`${t.retries} retr${t.retries > 1 ? "ies" : "y"}`}</Badge>}</div>
            <span>{t.description}</span><div className="small faint mono" style={{ wordBreak: "break-all" }}>{t.url}</div></div>
          <div className="row">
            <button className="btn sm" onClick={() => setTest({ tool: t, args: JSON.stringify(Object.fromEntries(t.parameters.map((p) => [p.name, p.type === "number" || p.type === "integer" ? 0 : p.type === "boolean" ? false : ""])), null, 2) })}><FlaskConical size={13} />Test</button>
            {!readOnly && <><button className="btn icon sm" onClick={() => open(t)}><Pencil size={13} /></button>
              <button className="btn icon sm danger" onClick={() => del(t)}><Trash2 size={13} /></button></>}
          </div>
        </div>)) : <Empty icon={<Wrench size={26} />} title="No functions yet" text="Example: check_order_status(order_id) -> GET https://api.yourshop.com/orders/{{order_id}}" />}

      <Modal open={!!edit} onClose={() => setEdit(null)} wide title={edit?.id ? `Edit ${edit.name}` : "New function"}
        footer={<><button className="btn" onClick={() => setEdit(null)}>Cancel</button><button className="btn primary" disabled={!edit?.name || !edit?.url || !edit?.description} onClick={save}>Save</button></>}>
        {edit && <>
          <div className="grid g2">
            <Field label="Name" hint="Letters, numbers, underscore - e.g. check_order_status"><input className="input mono" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value.replace(/\s/g, "_") })} /></Field>
            <Field label="Enabled"><div style={{ paddingTop: 6 }}><Toggle on={edit.enabled} onChange={(v) => setEdit({ ...edit, enabled: v })} /></div></Field>
          </div>
          <Field label="When should the agent use it?" hint="The model reads this to decide when to call the function"><textarea className="input" style={{ minHeight: 60 }} value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
          <div className="grid" style={{ gridTemplateColumns: "120px 1fr" }}>
            <Field label="Method"><select className="input" value={edit.method} onChange={(e) => setEdit({ ...edit, method: e.target.value })}>{["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m}>{m}</option>)}</select></Field>
            <Field label="URL" hint="GET: parameters are added to the query string. POST: sent as JSON with _call and _variables."><input className="input mono" placeholder="https://api.example.com/orders/{{order_id}}" value={edit.url} onChange={(e) => setEdit({ ...edit, url: e.target.value })} /></Field>
          </div>
          <div className="section-title">Parameters the agent collects</div>
          {edit.parameters.map((p, i) => (
            <div key={i} className="params">
              <input className="input sm mono" placeholder="order_id" value={p.name} onChange={(e) => setP(i, "name", e.target.value)} />
              <select className="input sm" value={p.type} onChange={(e) => setP(i, "type", e.target.value)}>{["string", "number", "integer", "boolean"].map((t) => <option key={t}>{t}</option>)}</select>
              <input className="input sm" placeholder="What it is (helps the agent ask for it)" value={p.description} onChange={(e) => setP(i, "description", e.target.value)} />
              <label className="row small" style={{ gap: 4 }}><input type="checkbox" checked={p.required} onChange={(e) => setP(i, "required", e.target.checked)} />required</label>
              <button className="btn icon sm" onClick={() => setEdit({ ...edit, parameters: edit.parameters.filter((_, j) => j !== i) })}><X size={13} /></button>
            </div>))}
          <div><button className="btn sm" onClick={() => setEdit({ ...edit, parameters: [...edit.parameters, { name: "", type: "string", description: "", required: true }] })}><Plus size={13} />Add parameter</button></div>
          <div className="section-title">Headers</div>
          {hdrs.map(([k, v], i) => (
            <div key={i} className="kv">
              <input className="input sm mono" placeholder="Authorization" value={k} onChange={(e) => setHdrs(hdrs.map((h, j) => (j === i ? [e.target.value, h[1]] : h)))} />
              <input className="input sm mono" placeholder="Bearer xxx" value={v} onChange={(e) => setHdrs(hdrs.map((h, j) => (j === i ? [h[0], e.target.value] : h)))} />
              <button className="btn icon sm" onClick={() => setHdrs(hdrs.filter((_, j) => j !== i))}><X size={13} /></button>
            </div>))}
          <div><button className="btn sm" onClick={() => setHdrs([...hdrs, ["", ""]])}><Plus size={13} />Add header</button></div>
          <div className="grid g3">
            <Field label="Say while waiting" hint="Optional, e.g. 'Let me check that for you'"><input className="input" value={edit.wait_message || ""} onChange={(e) => setEdit({ ...edit, wait_message: e.target.value })} /></Field>
            <Field label="Timeout (seconds)"><input className="input" type="number" min={2} max={30} value={edit.timeout_sec} onChange={(e) => setEdit({ ...edit, timeout_sec: Number(e.target.value) })} /></Field>
            <Field label="Retries" hint="On network errors and 5xx"><select className="input" value={edit.retries} onChange={(e) => setEdit({ ...edit, retries: Number(e.target.value) })}>{[0, 1, 2, 3].map((n) => <option key={n}>{n}</option>)}</select></Field>
          </div>
        </>}
      </Modal>

      <Modal open={!!test} onClose={() => setTest(null)} title={`Test ${test?.tool.name}`}
        footer={<button className="btn primary" disabled={test?.busy} onClick={runTest}><FlaskConical size={14} />{test?.busy ? "Calling..." : "Call API"}</button>}>
        <Field label="Arguments (JSON)" hint="Uses the saved function. Variables like {{caller_number}} get test values."><textarea className="input mono" style={{ minHeight: 110 }} value={test?.args || ""} onChange={(e) => setTest({ ...test!, args: e.target.value })} /></Field>
        {test?.result !== undefined && <pre className="code" style={{ display: "block", whiteSpace: "pre-wrap", maxHeight: 240, overflow: "auto" }}>{test.result}</pre>}
      </Modal>
    </>
  );
}
