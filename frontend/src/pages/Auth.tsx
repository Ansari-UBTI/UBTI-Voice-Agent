import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AudioLines, Globe2, Languages, Mic2, PhoneCall } from "lucide-react";
import { useAuth } from "../auth";
import { Field } from "../components/ui";

function Art() {
  const f = [
    [PhoneCall, "Connect any number", "Bring your Vobiz or Twilio number - inbound & outbound in minutes."],
    [Languages, "Speaks like your customers", "English, Hindi, Tamil, Arabic & more - switches language mid-call."],
    [Mic2, "Human-like & cloned voices", "Ultra-low latency realtime voice with your own brand voice."],
    [Globe2, "India · UAE · US ready", "Multi-tenant, secure, built on Microsoft Azure."],
  ] as const;
  return (
    <div className="auth-art">
      <div className="row" style={{ zIndex: 1 }}><div className="brand-logo" style={{ background: "rgba(255,255,255,.2)" }}><AudioLines size={20} /></div><b style={{ fontSize: 17 }}>UBTI Voice Agent</b></div>
      <div style={{ zIndex: 1 }}>
        <h1>AI voice agents that sound truly human.</h1>
        {f.map(([I, t, d]) => (
          <div className="auth-feat" key={t}><div className="i"><I size={17} /></div><div><b>{t}</b><div style={{ opacity: .85, fontSize: 13 }}>{d}</div></div></div>
        ))}
      </div>
      <div style={{ opacity: .7, fontSize: 12, zIndex: 1 }}>Powered by Azure Voice Live · gpt-realtime</div>
    </div>
  );
}

export function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { await login(email, password); nav("/"); } catch (x: any) { setErr(x.message); }
    setBusy(false);
  };
  return (
    <div className="auth"><Art />
      <div className="auth-form">
        <form className="auth-box" onSubmit={submit}>
          <div><h2>Welcome back</h2><p className="muted">Sign in to manage your voice agents</p></div>
          <Field label="Work email"><input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" /></Field>
          <Field label="Password"><input className="input" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" /></Field>
          {err && <div className="small" style={{ color: "var(--danger)" }}>{err}</div>}
          <button className="btn primary lg" disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button>
          <div className="muted small" style={{ textAlign: "center" }}>New to UBTI? <Link to="/signup">Create an account</Link></div>
        </form>
      </div>
    </div>
  );
}

export function Signup() {
  const { signup } = useAuth();
  const nav = useNavigate();
  const [d, setD] = useState({ company: "", name: "", email: "", password: "", country: "IN" });
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const set = (k: string) => (e: any) => setD({ ...d, [k]: e.target.value });
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { await signup(d); nav("/"); } catch (x: any) { setErr(x.message); }
    setBusy(false);
  };
  return (
    <div className="auth"><Art />
      <div className="auth-form">
        <form className="auth-box" onSubmit={submit}>
          <div><h2>Create your account</h2><p className="muted">Start free with 100 minutes · no card needed</p></div>
          <Field label="Company name"><input className="input" required value={d.company} onChange={set("company")} placeholder="Acme Realty" /></Field>
          <Field label="Your name"><input className="input" required value={d.name} onChange={set("name")} /></Field>
          <Field label="Work email"><input className="input" type="email" required value={d.email} onChange={set("email")} /></Field>
          <div className="grid g2">
            <Field label="Password"><input className="input" type="password" minLength={8} required value={d.password} onChange={set("password")} /></Field>
            <Field label="Country">
              <select className="input" value={d.country} onChange={set("country")}><option value="IN">India</option><option value="AE">UAE</option><option value="US">United States</option><option value="SG">Singapore</option></select>
            </Field>
          </div>
          {err && <div className="small" style={{ color: "var(--danger)" }}>{err}</div>}
          <button className="btn primary lg" disabled={busy}>{busy ? "Creating..." : "Create account"}</button>
          <div className="muted small" style={{ textAlign: "center" }}>Already have an account? <Link to="/login">Sign in</Link></div>
        </form>
      </div>
    </div>
  );
}
