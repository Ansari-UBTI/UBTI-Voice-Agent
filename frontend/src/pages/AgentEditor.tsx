import { ReactNode, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft, BarChart2, BookOpen, Bot, GitBranch, Mic2, Pause, PhoneForwarded, Play, Plus, Save, Settings2,
  SlidersHorizontal, Upload, Wrench, X,
} from "lucide-react";
import { api } from "../api";
import { can, useAuth } from "../auth";
import TestCall from "../components/TestCall";
import FunctionsTab from "../components/agent/FunctionsTab";
import VersionsTab from "../components/agent/VersionsTab";
import { Badge, Field, Loading, Modal, Tabs, Toggle, useToast } from "../components/ui";

export function usePreview() {
  const [playing, setPlaying] = useState("");
  const audio = useRef<HTMLAudioElement | null>(null);
  const toast = useToast();
  const play = async (key: string, url: string) => {
    if (playing === key) { audio.current?.pause(); setPlaying(""); return; }
    audio.current?.pause(); setPlaying(key);
    try {
      const blob: Blob = await api(url);
      const a = new Audio(URL.createObjectURL(blob)); audio.current = a;
      a.onended = () => setPlaying(""); await a.play();
    } catch (e: any) { toast(e.message, "error"); setPlaying(""); }
  };
  return { playing, play };
}

const COLORS = ["#0ea5e9", "#6d5ef5", "#12a37a", "#e0475b", "#d98a07", "#2563eb", "#0891b2"];

function Setting({ title, text, on, onChange, children }: { title: string; text: string; on?: boolean; onChange?: (v: boolean) => void; children?: ReactNode }) {
  return (<div>
    <div className="setting"><div className="txt"><b>{title}</b><span>{text}</span></div>{onChange && <Toggle on={!!on} onChange={onChange} />}</div>
    {children && (on === undefined || on) && <div className="sub stack" style={{ gap: 12 }}>{children}</div>}
  </div>);
}

const Num = ({ v, set, min, max, step = 1 }: { v: any; set: (n: number) => void; min: number; max: number; step?: number }) =>
  <input className="input" type="number" min={min} max={max} step={step} value={v ?? 0} onChange={(e) => set(Number(e.target.value))} />;

export default function AgentEditor() {
  const { id } = useParams(); const nav = useNavigate(); const toast = useToast(); const { user } = useAuth();
  const ro = !can(user, "member");
  const [a, setA] = useState<any>(null); const [tab, setTab] = useState("agent");
  const [meta, setMeta] = useState<any>({ languages: [], models: [], system_variables: [] });
  const [voices, setVoices] = useState<any>({ premade: [], cloned: [] });
  const [docs, setDocs] = useState<any[]>([]);
  const [dirty, setDirty] = useState(false); const [saving, setSaving] = useState(false);
  const [pub, setPub] = useState<string | null>(null);
  const [vf, setVf] = useState("all");
  const [newVar, setNewVar] = useState(["", ""]);
  const { playing, play } = usePreview();

  const reload = () => api(`/api/agents/${id}`).then((x) => { setA(x); setDirty(false); });
  useEffect(() => {
    api(`/api/agents/${id}`).then(setA).catch(() => nav("/agents"));
    api("/api/agents/meta").then(setMeta); api("/api/voices").then(setVoices); api("/api/knowledge").then(setDocs);
  }, [id]);
  if (!a) return <Loading />;
  const set = (k: string, v: any) => { setA({ ...a, [k]: v }); setDirty(true); };
  const save = async () => {
    setSaving(true);
    try { setA(await api(`/api/agents/${id}`, { method: "PUT", body: a })); setDirty(false); toast("Draft saved", "success"); }
    catch (e: any) { toast(e.message, "error"); }
    setSaving(false);
  };
  const publish = async () => {
    try {
      if (dirty) await api(`/api/agents/${id}`, { method: "PUT", body: a });
      const r = await api(`/api/agents/${id}/publish`, { body: { note: pub || "" } });
      setA(r.agent); setDirty(false); setPub(null); toast(`Published v${r.version} - live calls use it now`, "success");
    } catch (e: any) { toast(e.message, "error"); }
  };
  const premade = voices.premade.filter((v: any) => vf === "all" || (vf === "openai" ? v.type === "openai" : v.locale.startsWith(vf)));
  const myDocs = docs.filter((d) => !d.agent_id || d.agent_id === a.id);
  const kbChars = myDocs.reduce((s, d) => s + (d.chars || 0), 0);
  const insertVar = (k: string, name: string) => set(k, `${a[k] || ""}{{${name}}}`);
  const fields: any[] = a.analysis_fields || [];
  const setField = (i: number, k: string, v: any) => set("analysis_fields", fields.map((f, j) => (j === i ? { ...f, [k]: v } : f)));
  const missing: string[] = (a.variables_used || []).filter((v: string) => !(v in (a.variables || {})) && !(meta.system_variables || []).includes(v));
  const allVars = Array.from(new Set([...(meta.system_variables || []), ...Object.keys(a.variables || {}), ...(a.variables_used || [])]));

  return (
    <div className="stack">
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div className="row">
          <button className="btn icon" onClick={() => nav("/agents")}><ArrowLeft size={17} /></button>
          <div><input className="input" disabled={ro} style={{ fontSize: 19, fontWeight: 700, border: 0, padding: "2px 6px", background: "transparent" }} value={a.name} onChange={(e) => set("name", e.target.value)} />
            <div className="row small muted wrap" style={{ paddingLeft: 6 }}>Agent ID <span className="mono">{a.id.slice(0, 8)}</span> <Badge>{a.status}</Badge>
              {a.published_version ? <Badge tone="green">{`live v${a.published_version}`}</Badge> : <Badge tone="amber">not published</Badge>}
              {(a.has_unpublished_changes || dirty) && <Badge tone="amber">draft has unpublished changes</Badge>}</div></div>
        </div>
        {!ro && <div className="actions">
          <div className="row small"><span className="muted">Active</span><Toggle on={a.status === "active"} onChange={async (v) => { try { const x = await api(`/api/agents/${id}`, { method: "PUT", body: { status: v ? "active" : "paused" } }); setA({ ...a, status: x.status }); toast(v ? "Agent active" : "Agent paused - it will not answer calls", "success"); } catch (e: any) { toast(e.message, "error"); } }} /></div>
          <button className="btn" onClick={save} disabled={!dirty || saving}><Save size={16} />{saving ? "Saving..." : dirty ? "Save draft" : "Saved"}</button>
          <button className="btn primary" onClick={() => setPub("")} disabled={!dirty && !a.has_unpublished_changes}><Upload size={16} />Publish</button>
        </div>}
      </div>

      <div className="grid g-2-1" style={{ alignItems: "start" }}>
        <div className="card">
          <div style={{ padding: "0 12px" }}>
            <Tabs value={tab} onChange={setTab} tabs={[
              { id: "agent", label: "Agent", icon: <Bot size={15} /> }, { id: "voice", label: "Voice", icon: <Mic2 size={15} /> },
              { id: "handling", label: "Call handling", icon: <Settings2 size={15} /> }, { id: "transfer", label: "Transfer", icon: <PhoneForwarded size={15} /> },
              { id: "functions", label: "Functions", icon: <Wrench size={15} /> }, { id: "analysis", label: "Post-call", icon: <BarChart2 size={15} /> },
              { id: "knowledge", label: "Knowledge", icon: <BookOpen size={15} /> }, { id: "behavior", label: "Model", icon: <SlidersHorizontal size={15} /> },
              { id: "versions", label: "Versions", icon: <GitBranch size={15} /> },
            ]} />
          </div>
          <fieldset disabled={ro && tab !== "versions"} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <div className="card-pad stack">
            {tab === "agent" && <>
              <div className="grid g2">
                <Field label="Who speaks first">
                  <div className="seg">{[["agent", "Agent"], ["user", "Caller"]].map(([k, l]) => <button type="button" key={k} className={(a.start_speaker || "agent") === k ? "on" : ""} onClick={() => set("start_speaker", k)}>{l}</button>)}</div>
                </Field>
                <Field label="Pause before speaking (ms)" hint="Small delay sounds more natural on outbound calls"><Num v={a.begin_delay_ms} min={0} max={5000} step={100} set={(n) => set("begin_delay_ms", n)} /></Field>
              </div>
              {(a.start_speaker || "agent") === "agent" && <Field label="First message" hint="Leave empty to let the agent write its own greeting from the prompt.">
                <textarea className="input" style={{ minHeight: 64 }} value={a.first_message || ""} onChange={(e) => set("first_message", e.target.value)} />
              </Field>}
              <Field label="System prompt" hint="Personality, goal, rules and what to collect. Use {{variables}} for per-call data. Phone speaking style is added automatically.">
                <textarea className="input" style={{ minHeight: 260 }} value={a.system_prompt || ""} onChange={(e) => set("system_prompt", e.target.value)} />
              </Field>
              <div className="row wrap" style={{ gap: 6 }}><span className="small muted">Insert variable:</span>{allVars.map((v) => <button type="button" key={v} className="chip" onClick={() => insertVar("system_prompt", v)}>{`{{${v}}}`}</button>)}</div>
              <div className="section-title">Dynamic variables - default values</div>
              <div className="small muted">Filled per call from the outbound API (<span className="mono">variables</span>), batch CSV columns, or these defaults. System variables (time, numbers) are always available.</div>
              {Object.entries(a.variables || {}).map(([k, v]) => (
                <div key={k} className="kv"><input className="input sm mono" value={k} disabled /><input className="input sm" value={String(v)} onChange={(e) => set("variables", { ...a.variables, [k]: e.target.value })} />
                  <button type="button" className="btn icon sm" onClick={() => { const n = { ...a.variables }; delete n[k]; set("variables", n); }}><X size={13} /></button></div>))}
              <div className="kv"><input className="input sm mono" placeholder="customer_name" value={newVar[0]} onChange={(e) => setNewVar([e.target.value.replace(/[^A-Za-z0-9_]/g, "_"), newVar[1]])} />
                <input className="input sm" placeholder="default value" value={newVar[1]} onChange={(e) => setNewVar([newVar[0], e.target.value])} />
                <button type="button" className="btn sm" disabled={!newVar[0]} onClick={() => { set("variables", { ...(a.variables || {}), [newVar[0]]: newVar[1] }); setNewVar(["", ""]); }}><Plus size={13} />Add</button></div>
              {missing.length > 0 && <div className="small" style={{ color: "var(--warn)" }}>Used without a default: {missing.join(", ")} - empty unless the call provides them.</div>}
              <div className="grid g2">
                <Field label="Primary language">
                  <select className="input" value={a.language} onChange={(e) => set("language", e.target.value)}>
                    {meta.languages.map((l: any) => <option key={l.code} value={l.code}>{l.name}</option>)}
                  </select>
                </Field>
                <Field label="Auto-detect & switch language" hint="Replies in whatever language the caller speaks">
                  <div style={{ paddingTop: 6 }}><Toggle on={!!a.auto_detect_language} onChange={(v) => set("auto_detect_language", v)} /></div>
                </Field>
              </div>
            </>}

            {tab === "voice" && <>
              <div className="row between wrap">
                <div className="seg">{[["all", "All"], ["en-IN", "Indian English"], ["hi", "Hindi"], ["ta", "Tamil"], ["ar", "Arabic"], ["en-US", "US"], ["openai", "OpenAI"]].map(([k, l]) =>
                  <button type="button" key={k} className={vf === k ? "on" : ""} onClick={() => setVf(k)}>{l}</button>)}</div>
              </div>
              {voices.cloned.filter((v: any) => v.status === "ready").length > 0 && <>
                <b className="small muted">YOUR CLONED VOICES</b>
                <div className="grid g2">{voices.cloned.filter((v: any) => v.status === "ready").map((v: any) => (
                  <div key={v.id} className={`card voice ${a.speaker_profile_id === v.speaker_profile_id && a.voice_type === "azure-personal" ? "selected" : ""}`} style={{ cursor: "pointer" }}
                    onClick={() => { if (!ro) { setA({ ...a, voice_type: "azure-personal", speaker_profile_id: v.speaker_profile_id, voice_name: v.name }); setDirty(true); } }}>
                    <div className="voice-ava" style={{ background: "var(--grad)" }}><Mic2 size={18} /></div>
                    <div style={{ flex: 1 }}><b>{v.name}</b><div className="small muted">Cloned · {v.locale}</div></div>
                    <button type="button" className="btn icon sm" onClick={(e) => { e.stopPropagation(); play(v.id, `/api/voices/preview?voice_id=${v.id}`); }}>{playing === v.id ? <Pause size={14} /> : <Play size={14} />}</button>
                  </div>))}</div>
              </>}
              <div className="grid g2">{premade.map((v: any, i: number) => {
                const sel = a.voice_type === v.type && a.voice_name === v.id;
                return (
                  <div key={v.id} className={`card voice ${sel ? "selected" : ""}`} style={{ cursor: "pointer" }}
                    onClick={() => { if (!ro) { setA({ ...a, voice_type: v.type, voice_name: v.id, speaker_profile_id: null }); setDirty(true); } }}>
                    <div className="voice-ava" style={{ background: COLORS[i % COLORS.length] }}>{v.name[0]}</div>
                    <div style={{ flex: 1, minWidth: 0 }}><div className="row" style={{ gap: 6 }}><b>{v.name}</b>{v.hd && <Badge tone="violet">HD</Badge>}</div>
                      <div className="small muted">{v.gender} · {v.accent} · {v.style}</div></div>
                    {v.type !== "openai" && <button type="button" className="btn icon sm" onClick={(e) => { e.stopPropagation(); play(v.id, `/api/voices/preview?voice=${encodeURIComponent(v.id)}&locale=${v.locale}`); }}>{playing === v.id ? <Pause size={14} /> : <Play size={14} />}</button>}
                  </div>);
              })}</div>
              {a.voice_type === "azure-standard" && (
                <Field label={`Speaking rate · ${Number(a.voice_rate || 1).toFixed(2)}x`} hint="Slightly slower (0.95) often sounds more natural on phone calls">
                  <input type="range" min={0.8} max={1.2} step={0.05} value={a.voice_rate || 1} onChange={(e) => set("voice_rate", Number(e.target.value))} />
                </Field>
              )}
              <Field label="Boosted keywords" hint="Names, brands and product words the transcription should recognise - comma or one per line (max 100)">
                <textarea className="input" style={{ minHeight: 70 }} placeholder={"Prashanti, Wedtree, FiLLi\nKanjivaram"} value={a.boosted_keywords || ""} onChange={(e) => set("boosted_keywords", e.target.value)} />
              </Field>
            </>}

            {tab === "handling" && <>
              <Setting title="End call on silence" text="Hang up politely when the caller stays silent this long" on={(a.silence_end_seconds || 0) > 0} onChange={(v) => set("silence_end_seconds", v ? 30 : 0)}>
                <Field label="Silence before ending (seconds)"><Num v={a.silence_end_seconds} min={5} max={3600} set={(n) => set("silence_end_seconds", n)} /></Field>
              </Setting>
              <Setting title="Check in when the caller goes quiet" text="'Are you still there?' after a pause" on={(a.reminder_seconds || 0) > 0} onChange={(v) => set("reminder_seconds", v ? 8 : 0)}>
                <div className="grid g2">
                  <Field label="After (seconds of quiet)"><Num v={a.reminder_seconds} min={2} max={120} set={(n) => set("reminder_seconds", n)} /></Field>
                  <Field label="Max reminders per pause"><Num v={a.reminder_max} min={1} max={10} set={(n) => set("reminder_max", n)} /></Field>
                </div>
              </Setting>
              <Setting title="Voicemail detection (outbound)" text="Carrier detection (Twilio / Vobiz) plus the agent recognising a voicemail greeting" on={!!a.voicemail_detection} onChange={(v) => set("voicemail_detection", v)}>
                <div className="seg">{[["hangup", "Hang up"], ["leave_message", "Leave a message"]].map(([k, l]) => <button type="button" key={k} className={(a.voicemail_action || "hangup") === k ? "on" : ""} onClick={() => set("voicemail_action", k)}>{l}</button>)}</div>
                {a.voicemail_action === "leave_message" && <Field label="Voicemail message" hint="Said after the beep. Variables allowed."><textarea className="input" style={{ minHeight: 60 }} placeholder="Hi {{customer_name}}, this is Sara from ... please call us back on ..." value={a.voicemail_message || ""} onChange={(e) => set("voicemail_message", e.target.value)} /></Field>}
              </Setting>
              <Setting title="Hang up on IVR (outbound)" text="End the call if it reaches an automated phone menu instead of a person" on={!!a.ivr_hangup} onChange={(v) => set("ivr_hangup", v)} />
              <Setting title="Call screening message" text="What to say when iPhone / Android call screening asks who is calling">
                <input className="input" placeholder="This is Sara from Wedtree about your order" value={a.screening_message || ""} onChange={(e) => set("screening_message", e.target.value)} />
              </Setting>
              <Setting title="Keypad input (DTMF)" text="Let callers type digits - order numbers, PINs, menu choices. Works on Twilio, ACS and the browser test." on={!!a.dtmf_enabled} onChange={(v) => set("dtmf_enabled", v)}>
                <div className="grid g3">
                  <Field label="Respond after (ms) of no key"><Num v={a.dtmf_timeout_ms} min={500} max={20000} step={500} set={(n) => set("dtmf_timeout_ms", n)} /></Field>
                  <Field label="Termination key"><select className="input" value={a.dtmf_terminator || ""} onChange={(e) => set("dtmf_terminator", e.target.value)}><option value="">None</option>{["#", "*", ..."0123456789"].map((k) => <option key={k}>{k}</option>)}</select></Field>
                  <Field label="Digit limit" hint="0 = no limit"><Num v={a.dtmf_max_digits} min={0} max={50} set={(n) => set("dtmf_max_digits", n)} /></Field>
                </div>
              </Setting>
              <div className="grid g2">
                <Field label="Ring duration (seconds)" hint="Outbound: give up after ringing this long"><Num v={a.ring_seconds} min={10} max={120} set={(n) => set("ring_seconds", n)} /></Field>
                <Field label="Max call duration (seconds)"><Num v={a.max_call_seconds} min={30} max={7200} set={(n) => set("max_call_seconds", n)} /></Field>
              </div>
              <Setting title="Record calls" text="Two-channel recording (caller left, agent right) in call history" on={a.recording_enabled !== false} onChange={(v) => set("recording_enabled", v)} />
              <Setting title="Agent can end the call" text="Hangs up politely once the conversation is complete" on={!!a.end_call_enabled} onChange={(v) => set("end_call_enabled", v)} />
            </>}

            {tab === "transfer" && <>
              <Field label="Transfer to (number)" hint="The agent transfers when the caller asks for a person or it cannot help. Leave empty to disable."><input className="input" placeholder="+9198XXXXXXXX" value={a.transfer_number || ""} onChange={(e) => set("transfer_number", e.target.value)} /></Field>
              <Field label="Transfer type">
                <div className="grid g2">{[["cold", "Cold transfer", "Connect the caller straight to the number."], ["warm", "Warm transfer", "Twilio / Vobiz: the human hears a short AI briefing first. ACS: the AI introduces the caller to the human, then leaves."]].map(([k, t, d]) => (
                  <div key={k} className={`tpl ${(a.transfer_mode || "cold") === k ? "on" : ""}`} onClick={() => !ro && set("transfer_mode", k)}><b>{t}</b><div className="small muted">{d}</div></div>))}</div>
              </Field>
              <Field label="Ring the human for (seconds)"><Num v={a.transfer_ring_seconds} min={10} max={120} set={(n) => set("transfer_ring_seconds", n)} /></Field>
              <div className="small muted">Works on Vobiz, Twilio and Azure (ACS) numbers. Browser tests only simulate the transfer.</div>
            </>}

            {tab === "functions" && <FunctionsTab agentId={a.id} readOnly={ro} onChanged={() => api(`/api/agents/${id}`).then((x) => setA({ ...a, has_unpublished_changes: x.has_unpublished_changes }))} />}

            {tab === "analysis" && <>
              <div className="muted">After every call the AI fills in a summary, sentiment, outcome, language, <b>call successful</b> and <b>reached voicemail</b>. Add your own fields below - they appear in call history, the CSV export and the <span className="mono">call.analyzed</span> webhook.</div>
              {fields.map((f, i) => (
                <div key={i} className="card card-pad stack" style={{ boxShadow: "none", gap: 10, padding: 14 }}>
                  <div className="grid" style={{ gridTemplateColumns: "1fr 160px auto" }}>
                    <input className="input sm mono" placeholder="field_name" value={f.name} onChange={(e) => setField(i, "name", e.target.value.replace(/[^A-Za-z0-9_]/g, "_").toLowerCase())} />
                    <select className="input sm" value={f.type} onChange={(e) => setField(i, "type", e.target.value)}>{["string", "number", "boolean", "enum"].map((t) => <option key={t}>{t}</option>)}</select>
                    <button type="button" className="btn icon sm" onClick={() => set("analysis_fields", fields.filter((_, j) => j !== i))}><X size={13} /></button>
                  </div>
                  <input className="input sm" placeholder="What to extract, e.g. 'Date the customer promised to pay (YYYY-MM-DD)'" value={f.description} onChange={(e) => setField(i, "description", e.target.value)} />
                  {f.type === "enum" && <input className="input sm" placeholder="Options, comma separated: hot, warm, cold" value={(f.options || []).join(", ")} onChange={(e) => setField(i, "options", e.target.value.split(",").map((x: string) => x.trim()).filter(Boolean))} />}
                </div>))}
              <div className="row wrap">
                <button type="button" className="btn sm" onClick={() => set("analysis_fields", [...fields, { name: "", type: "string", description: "", options: [] }])}><Plus size={13} />Add field</button>
                {[["lead_quality", "enum", "How qualified the lead is", ["hot", "warm", "cold"]], ["appointment_date", "string", "Booked appointment date/time, ISO format"], ["promise_to_pay_date", "string", "Date the customer promised to pay, YYYY-MM-DD"], ["callback_requested", "boolean", "Customer asked for a callback"]].map(([n, t, d, o]: any) =>
                  !fields.some((f) => f.name === n) && <button type="button" key={n} className="chip" onClick={() => set("analysis_fields", [...fields, { name: n, type: t, description: d, options: o || [] }])}>+ {n}</button>)}
              </div>
            </>}

            {tab === "knowledge" && <>
              <div className="row between"><span className="muted">The agent answers from these documents (shared docs + docs assigned to this agent).</span>
                <button type="button" className="btn sm" onClick={() => nav("/knowledge")}>Manage</button></div>
              <div className="small muted">{(kbChars / 1000).toFixed(1)}k characters - {kbChars <= 12000 ? "small enough to give the agent in full." : "the agent searches it during the call (only the relevant parts are used)."}</div>
              {myDocs.length ? myDocs.map((d) => (
                <div key={d.id} className="row between card card-pad" style={{ boxShadow: "none", padding: 12 }}>
                  <div className="row"><BookOpen size={16} color="var(--primary)" /><b>{d.name}</b></div>
                  <div className="row"><Badge tone="gray">{d.source_type}</Badge><Badge>{d.agent_id ? "This agent" : "Shared"}</Badge></div>
                </div>)) : <div className="muted">No documents yet.</div>}
            </>}

            {tab === "behavior" && <>
              <Field label="Realtime model">
                <div className="grid g2">{meta.models.map((m: any) => (
                  <div key={m.id} className={`tpl ${a.model === m.id ? "on" : ""}`} onClick={() => !ro && set("model", m.id)}><b>{m.name}</b><div className="small muted">{m.note}</div></div>
                ))}</div>
              </Field>
              <div className="grid g2">
                <Field label={`Creativity · ${Number(a.temperature).toFixed(1)}`} hint="Realtime models need at least 0.6">
                  <input type="range" min={0.6} max={1.2} step={0.1} value={a.temperature} onChange={(e) => set("temperature", Number(e.target.value))} />
                </Field>
                <Field label="Interruption sensitivity" hint="High = stops talking at the slightest sound">
                  <div className="seg">{["low", "medium", "high"].map((s) => <button type="button" key={s} className={a.interrupt_sensitivity === s ? "on" : ""} onClick={() => set("interrupt_sensitivity", s)} style={{ textTransform: "capitalize" }}>{s}</button>)}</div>
                </Field>
              </div>
            </>}

            {tab === "versions" && <VersionsTab agent={a} readOnly={ro} onChanged={(x) => { setA(x); setDirty(false); }} />}
          </div>
          </fieldset>
        </div>
        <div style={{ position: "sticky", top: 76 }}>
          <TestCall agentId={a.id} agentName={a.name} variables={Object.fromEntries([...(a.variables_used || []), ...Object.keys(a.variables || {})].filter((v) => !(meta.system_variables || []).includes(v)).map((v) => [v, a.variables?.[v] || ""]))} />
          {dirty && <div className="small" style={{ color: "var(--warn)", marginTop: 8, textAlign: "center" }}>Save the draft before testing - the test uses the saved draft</div>}
        </div>
      </div>

      <Modal open={pub !== null} onClose={() => setPub(null)} title="Publish this version"
        footer={<><button className="btn" onClick={() => setPub(null)}>Cancel</button><button className="btn primary" onClick={publish}><Upload size={15} />Publish v{(a.published_version || 0) + 1}</button></>}>
        <div className="muted">All new calls - inbound, outbound and batch - will use this version. Calls already in progress keep their version. You can roll back from the Versions tab.</div>
        <Field label="What changed? (optional)"><input className="input" value={pub || ""} onChange={(e) => setPub(e.target.value)} placeholder="Shorter greeting, added order lookup" /></Field>
      </Modal>
    </div>
  );
}
