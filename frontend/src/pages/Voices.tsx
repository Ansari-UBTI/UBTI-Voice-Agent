import { useEffect, useRef, useState } from "react";
import { Mic, Mic2, Pause, Play, ShieldCheck, Square, Trash2, Upload, Wand2 } from "lucide-react";
import { api, ago } from "../api";
import { Badge, Empty, Field, Loading, Modal, PageHeader, Tabs, useToast } from "../components/ui";
import { usePreview } from "./AgentEditor";

const COLORS = ["#0ea5e9", "#6d5ef5", "#12a37a", "#e0475b", "#d98a07", "#2563eb", "#0891b2"];

function Recorder({ label, onDone, file }: { label: string; onDone: (f: File) => void; file?: File | null }) {
  const [rec, setRec] = useState(false); const mr = useRef<MediaRecorder | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const start = async () => {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    const m = new MediaRecorder(s); const chunks: Blob[] = [];
    m.ondataavailable = (e) => chunks.push(e.data);
    m.onstop = () => { s.getTracks().forEach((t) => t.stop()); onDone(new File(chunks, `${label}.webm`, { type: "audio/webm" })); };
    m.start(); mr.current = m; setRec(true);
  };
  const stop = () => { mr.current?.stop(); setRec(false); };
  return (
    <div className="row wrap" style={{ gap: 14 }}>
      <button type="button" className={`rec-btn ${rec ? "on" : ""}`} onClick={rec ? stop : start}>{rec ? <Square size={22} /> : <Mic size={24} />}</button>
      <div style={{ flex: 1 }}>
        <div className="small muted">{rec ? "Recording... tap to stop" : "Record in a quiet room, or upload a WAV file"}</div>
        <button type="button" className="btn sm" style={{ marginTop: 6 }} onClick={() => input.current?.click()}><Upload size={14} />Upload file</button>
        <input ref={input} type="file" accept="audio/*" hidden onChange={(e) => e.target.files?.[0] && onDone(e.target.files[0])} />
        {file && <div className="small" style={{ marginTop: 6, color: "var(--success)" }}>✓ {file.name} ({Math.round(file.size / 1024)} KB)</div>}
      </div>
    </div>
  );
}

export default function Voices() {
  const toast = useToast();
  const [v, setV] = useState<any>(null); const [tab, setTab] = useState("library"); const [q, setQ] = useState("");
  const [open, setOpen] = useState(false); const [step, setStep] = useState(0);
  const [f, setF] = useState<any>({ name: "", locale: "en-IN", talent_name: "", company_name: "" });
  const [consent, setConsent] = useState<File | null>(null); const [sample, setSample] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const { playing, play } = usePreview();
  const load = () => api("/api/voices").then(setV);
  useEffect(() => { load(); }, []);
  if (!v) return <Loading />;

  const consentText = `I ${f.talent_name || "[your name]"} am aware that recordings of my voice will be used by ${f.company_name || "[company]"} to create and use a synthetic version of my voice.`;
  const submit = async () => {
    setBusy(true);
    const fd = new FormData(); Object.entries(f).forEach(([k, val]) => fd.append(k, String(val)));
    fd.append("consent_audio", consent!); fd.append("sample_audio", sample!);
    try { await api("/api/voices/clone", { form: fd }); toast("Submitted for review", "success"); setOpen(false); setStep(0); setConsent(null); setSample(null); setTab("cloned"); load(); }
    catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  const list = v.premade.filter((x: any) => `${x.name} ${x.accent} ${x.locale} ${x.style}`.toLowerCase().includes(q.toLowerCase()));

  return (
    <>
      <PageHeader title="Voices" subtitle="Pick a human-like voice or clone your own brand voice"
        actions={<button className="btn primary" onClick={() => setOpen(true)}><Wand2 size={16} />Clone a voice</button>} />
      <div className="card">
        <div style={{ padding: "0 12px" }} className="row between wrap">
          <Tabs value={tab} onChange={setTab} tabs={[{ id: "library", label: `Voice library (${v.premade.length})` }, { id: "cloned", label: `My cloned voices (${v.cloned.length})` }]} />
          {tab === "library" && <input className="input" style={{ maxWidth: 240, margin: 8 }} placeholder="Search voices, accents..." value={q} onChange={(e) => setQ(e.target.value)} />}
        </div>
        <div className="card-pad">
          {tab === "library" ? (
            <div className="grid g3">{list.map((x: any, i: number) => (
              <div key={x.id} className="card voice" style={{ boxShadow: "none" }}>
                <div className="voice-ava" style={{ background: COLORS[i % COLORS.length] }}>{x.name[0]}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="row" style={{ gap: 6 }}><b>{x.name}</b>{x.hd && <Badge tone="violet">HD</Badge>}</div>
                  <div className="small muted">{x.gender} · {x.accent}</div><div className="small faint">{x.style}</div>
                </div>
                {x.type !== "openai" ? <button className="btn icon" onClick={() => play(x.id, `/api/voices/preview?voice=${encodeURIComponent(x.id)}&locale=${x.locale}`)}>{playing === x.id ? <Pause size={15} /> : <Play size={15} />}</button> : <Badge tone="gray">native</Badge>}
              </div>))}</div>
          ) : v.cloned.length ? (
            <div className="table-wrap"><table className="table"><thead><tr><th>Voice</th><th>Locale</th><th>Status</th><th>Created</th><th></th></tr></thead>
              <tbody>{v.cloned.map((x: any) => (
                <tr key={x.id}><td><b>{x.name}</b>{x.error && <div className="small" style={{ color: "var(--danger)" }}>{x.error.slice(0, 90)}</div>}</td><td>{x.locale}</td>
                  <td><Badge live={x.status === "processing"}>{x.status}</Badge></td><td className="muted">{ago(x.created_at)}</td>
                  <td><div className="row" style={{ justifyContent: "flex-end" }}>
                    {x.status === "ready" && <button className="btn sm" onClick={() => play(x.id, `/api/voices/preview?voice_id=${x.id}`)}>{playing === x.id ? <Pause size={14} /> : <Play size={14} />}Preview</button>}
                    <button className="btn sm icon danger" onClick={async () => { if (confirm("Delete voice?")) { await api(`/api/voices/${x.id}`, { method: "DELETE" }); load(); } }}><Trash2 size={14} /></button></div></td></tr>))}</tbody></table></div>
          ) : <Empty icon={<Mic2 size={26} />} title="No cloned voices" text="Clone a voice from a short sample - perfect for brand ambassadors." />}
        </div>
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title="Clone a voice (Azure Personal Voice)"
        footer={<>
          {step > 0 && <button className="btn" onClick={() => setStep(step - 1)}>Back</button>}
          {step < 2 ? <button className="btn primary" disabled={step === 0 ? !(f.name && f.talent_name && f.company_name) : !consent} onClick={() => setStep(step + 1)}>Continue</button>
            : <button className="btn primary" disabled={!sample || busy} onClick={submit}>{busy ? "Uploading..." : "Submit for review"}</button>}
        </>}>
        <div className="steps">{[0, 1, 2].map((i) => <span key={i} className={i <= step ? "on" : ""} />)}</div>
        {step === 0 && <>
          <Field label="Voice name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. Priya - Brand Voice" /></Field>
          <div className="grid g2">
            <Field label="Voice talent full name"><input className="input" value={f.talent_name} onChange={(e) => setF({ ...f, talent_name: e.target.value })} /></Field>
            <Field label="Company name"><input className="input" value={f.company_name} onChange={(e) => setF({ ...f, company_name: e.target.value })} /></Field>
          </div>
          <Field label="Consent language"><select className="input" value={f.locale} onChange={(e) => setF({ ...f, locale: e.target.value })}>
            {["en-IN", "en-US", "hi-IN", "ta-IN", "ar-AE"].map((l) => <option key={l}>{l}</option>)}</select></Field>
        </>}
        {step === 1 && <>
          <div className="card card-pad" style={{ background: "var(--primary-50)", boxShadow: "none" }}>
            <div className="row small" style={{ color: "var(--primary-600)", fontWeight: 700 }}><ShieldCheck size={15} />Step 1 · Record the consent statement</div>
            <p style={{ fontSize: 15, lineHeight: 1.6, margin: "10px 0 0" }}>"{consentText}"</p>
          </div>
          <Recorder label="consent" onDone={setConsent} file={consent} />
          <div className="small muted">Microsoft requires the voice owner's recorded consent. The voice talent must read this exactly.</div>
        </>}
        {step === 2 && <>
          <div className="small" style={{ color: "var(--primary-600)", fontWeight: 700 }}>Step 2 · Voice sample (5 - 90 seconds)</div>
          <div className="muted small">Speak naturally - read a paragraph or describe your day. The same person as the consent recording.</div>
          <Recorder label="sample" onDone={setSample} file={sample} />
          <div className="small muted">After submission UBTI reviews the request, then Azure creates the voice in a few seconds. Tip: WAV, 16-24 kHz mono gives the best results.</div>
        </>}
      </Modal>
    </>
  );
}
