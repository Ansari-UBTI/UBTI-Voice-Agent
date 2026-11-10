import { useEffect, useRef, useState } from "react";
import { Grid3x3, Mic, PhoneOff, Phone } from "lucide-react";
import { getToken, WS_BASE } from "../api";

const WORKLET = `class Cap extends AudioWorkletProcessor{process(i){const c=i[0]&&i[0][0];if(c)this.port.postMessage(c.slice(0));return true}}registerProcessor('cap',Cap);`;

function toB64(f32: Float32Array) {
  const i16 = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) { const s = Math.max(-1, Math.min(1, f32[i])); i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff; }
  const u8 = new Uint8Array(i16.buffer); let bin = "";
  for (let i = 0; i < u8.length; i += 0x8000) bin += String.fromCharCode.apply(null, Array.from(u8.subarray(i, i + 0x8000)));
  return btoa(bin);
}
function fromB64(b64: string) {
  const bin = atob(b64); const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  const i16 = new Int16Array(u8.buffer); const f = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) f[i] = i16[i] / 0x8000;
  return f;
}

type Line = { role: string; text: string };

export default function TestCall({ agentId, agentName, variables = {} }: { agentId: string; agentName: string; variables?: Record<string, string> }) {
  const [state, setState] = useState<"idle" | "connecting" | "live">("idle");
  const [speaking, setSpeaking] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [err, setErr] = useState("");
  const [vars, setVars] = useState<Record<string, string>>(variables);
  const [pad, setPad] = useState(false);
  const r = useRef<any>({});
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => () => stop(), []);
  useEffect(() => { setVars((v) => ({ ...variables, ...Object.fromEntries(Object.entries(v).filter(([k]) => k in variables)) })); }, [JSON.stringify(Object.keys(variables))]);
  useEffect(() => { box.current?.scrollTo(0, box.current.scrollHeight); }, [lines]);

  async function start() {
    setErr(""); setLines([]); setState("connecting");
    try {
      const ctx = new AudioContext({ sampleRate: 24000 });
      await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" })));
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      const v = encodeURIComponent(JSON.stringify(Object.fromEntries(Object.entries(vars).filter(([, x]) => x))));
      const ws = new WebSocket(`${WS_BASE}/ws/test/${agentId}?draft=1&vars=${v}&token=${encodeURIComponent(getToken())}`);
      const src = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, "cap");
      const mute = ctx.createGain(); mute.gain.value = 0;
      src.connect(node); node.connect(mute); mute.connect(ctx.destination);
      let buf: number[] = [];
      node.port.onmessage = (e) => {
        buf.push(...(e.data as Float32Array));
        if (buf.length >= 1200 && ws.readyState === 1) { ws.send(JSON.stringify({ type: "audio", data: toB64(Float32Array.from(buf)) })); buf = []; }
      };
      r.current = { ctx, stream, ws, node, next: 0, sources: [] as AudioBufferSourceNode[] };
      ws.onopen = () => setState("live");
      ws.onclose = (e) => { if (e.code === 4401) setErr("Session expired - please sign in again."); stop(); };
      ws.onerror = () => setErr("Could not reach the backend. Is FastAPI running?");
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data);
        if (m.type === "audio") {
          const f = fromB64(m.data);
          const ab = ctx.createBuffer(1, f.length, 24000); ab.copyToChannel(f, 0);
          const s = ctx.createBufferSource(); s.buffer = ab; s.connect(ctx.destination);
          const at = Math.max(r.current.next, ctx.currentTime + 0.02);
          s.start(at); r.current.next = at + ab.duration; r.current.sources.push(s);
          setSpeaking(true);
          s.onended = () => { r.current.sources = r.current.sources.filter((x: any) => x !== s); if (!r.current.sources.length) setSpeaking(false); };
        } else if (m.type === "clear") {
          r.current.sources.forEach((s: AudioBufferSourceNode) => { try { s.stop(); } catch {} });
          r.current.sources = []; r.current.next = 0; setSpeaking(false);
        } else if (m.type === "transcript") {
          setLines((l) => [...l, { role: m.role, text: m.text }]);
        }
      };
    } catch (e: any) {
      setErr(e?.message || "Microphone permission denied"); stop();
    }
  }

  function stop() {
    const c = r.current;
    try { c.ws?.readyState === 1 && c.ws.send(JSON.stringify({ type: "stop" })); c.ws?.close(); } catch {}
    try { c.stream?.getTracks().forEach((t: MediaStreamTrack) => t.stop()); } catch {}
    try { c.ctx?.close(); } catch {}
    r.current = {}; setState("idle"); setSpeaking(false);
  }
  const key = (d: string) => { const ws = r.current.ws; if (ws?.readyState === 1) ws.send(JSON.stringify({ type: "dtmf", digit: d })); };

  return (
    <div className="card">
      <div className="card-head"><h3>Test your agent</h3>{state === "live" ? <span className="badge green"><span className="dot live" />Live</span> : <span className="badge gray">uses the draft</span>}</div>
      <div className="orb-wrap"><div className={`orb ${state === "live" ? (speaking ? "speaking" : "live") : ""}`} /></div>
      <div style={{ textAlign: "center" }} className="muted small">
        {state === "idle" ? `Talk to ${agentName} right from your browser` : state === "connecting" ? "Connecting..." : speaking ? "Agent speaking - interrupt anytime" : "Listening..."}
      </div>
      {state === "idle" && Object.keys(vars).length > 0 && <div className="stack" style={{ padding: "12px 16px 0", gap: 6 }}>
        <div className="section-title">Test values for variables</div>
        {Object.entries(vars).map(([k, v]) => <div key={k} className="row"><span className="mono small" style={{ width: 120, overflow: "hidden", textOverflow: "ellipsis" }}>{k}</span><input className="input sm" value={v} onChange={(e) => setVars({ ...vars, [k]: e.target.value })} /></div>)}
      </div>}
      <div className="row" style={{ justifyContent: "center", padding: 16 }}>
        {state === "idle"
          ? <button className="btn primary lg" onClick={start}><Phone size={17} />Start call</button>
          : <><button className="btn danger lg" onClick={stop}><PhoneOff size={17} />End call</button><button className={`btn icon ${pad ? "primary" : ""}`} title="Keypad" onClick={() => setPad(!pad)}><Grid3x3 size={17} /></button></>}
      </div>
      {state === "live" && pad && <div className="keypad">{"123456789*0#".split("").map((d) => <button key={d} onClick={() => key(d)}>{d}</button>)}</div>}
      {err && <div className="small" style={{ color: "var(--danger)", textAlign: "center", paddingBottom: 12 }}>{err}</div>}
      <div ref={box} className="chat" style={{ maxHeight: 260, overflowY: "auto", padding: "0 16px 16px" }}>
        {lines.map((l, i) => <div key={i} className={`bubble ${l.role}`}><small>{l.role === "user" ? "You" : l.role === "dtmf" ? "Keypad" : l.role}</small>{l.text}</div>)}
        {!lines.length && state === "live" && <div className="faint small" style={{ textAlign: "center" }}><Mic size={13} /> Say hello...</div>}
      </div>
    </div>
  );
}
