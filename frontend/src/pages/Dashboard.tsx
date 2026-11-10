import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ArrowRight, BookOpen, Bot, Clock, Phone, PhoneCall, Smile, TrendingUp, Wallet } from "lucide-react";
import { api, ago, fmtDur } from "../api";
import { useAuth } from "../auth";
import { Badge, Empty, Loading, Stat } from "../components/ui";

export default function Dashboard() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [d, setD] = useState<any>(null);
  useEffect(() => { api("/api/analytics/overview?days=30").then(setD).catch(() => setD({ error: true })); }, []);
  if (!d) return <Loading />;
  const k = d.kpis || {}; const u = d.usage || { minutes_used: 0, minutes_quota: 1 };
  const pct = Math.min(100, (u.minutes_used / (u.minutes_quota || 1)) * 100);
  const steps = [
    { done: d.counts?.agents > 0, label: "Create your first agent", to: "/agents", icon: Bot },
    { done: d.counts?.docs > 0, label: "Add knowledge (FAQs, PDFs, website)", to: "/knowledge", icon: BookOpen },
    { done: d.counts?.numbers > 0, label: "Connect a phone number", to: "/numbers", icon: Phone },
    { done: k.total_calls > 0, label: "Make your first call", to: "/agents", icon: PhoneCall },
  ];

  return (
    <div className="stack">
      <div className="hero">
        <h2>Good to see you, {user?.name?.split(" ")[0]} 👋</h2>
        <p>Build human-sounding voice agents that answer calls, qualify leads and book appointments - in your customers' language.</p>
        <div className="row wrap" style={{ position: "relative", zIndex: 1 }}>
          <button className="btn white" onClick={() => nav("/agents")}><Bot size={16} />Create agent</button>
          <button className="btn" style={{ background: "rgba(255,255,255,.15)", color: "#fff", borderColor: "rgba(255,255,255,.3)" }} onClick={() => nav("/numbers")}><Phone size={16} />Connect number</button>
        </div>
        <div className="wave hide-sm">{[0, .2, .4, .1, .3, .5, .15].map((x, i) => <span key={i} style={{ animationDelay: `${x}s` }} />)}</div>
      </div>

      <div className="grid g4">
        <Stat icon={<PhoneCall size={19} />} label="Calls (30 days)" value={k.total_calls || 0} />
        <Stat icon={<Clock size={19} />} label="Avg. call duration" value={fmtDur(Math.round(k.avg_seconds || 0))} tone="cyan" />
        <Stat icon={<TrendingUp size={19} />} label="Success rate" value={`${k.success_rate || 0}%`} tone="green" />
        <Stat icon={<Wallet size={19} />} label="Spend (USD)" value={`$${(k.cost_usd || 0).toFixed(2)}`} tone="violet" />
      </div>

      <div className="grid g-2-1">
        <div className="card">
          <div className="card-head"><h3>Call volume</h3><span className="muted small">Last 30 days</span></div>
          <div style={{ height: 260, padding: "12px 8px 8px 0" }}>
            {d.daily?.length ? (
              <ResponsiveContainer>
                <AreaChart data={d.daily}>
                  <defs><linearGradient id="g1" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#0ea5e9" stopOpacity={.35} /><stop offset="1" stopColor="#0ea5e9" stopOpacity={0} /></linearGradient></defs>
                  <CartesianGrid stroke="#edf3fa" vertical={false} />
                  <XAxis dataKey="day" tick={{ fontSize: 11, fill: "#8aa0bb" }} tickFormatter={(v) => v.slice(5)} axisLine={false} tickLine={false} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#8aa0bb" }} axisLine={false} tickLine={false} width={34} />
                  <Tooltip contentStyle={{ borderRadius: 10, border: "1px solid #dbe8f6" }} />
                  <Area type="monotone" dataKey="calls" stroke="#0e8fe0" strokeWidth={2.5} fill="url(#g1)" />
                </AreaChart>
              </ResponsiveContainer>
            ) : <Empty title="No calls yet" text="Your call activity will appear here." />}
          </div>
        </div>
        <div className="stack">
          <div className="card card-pad">
            <div className="row between"><h3 style={{ fontSize: 15 }}>Minutes this month</h3><Badge>{user?.plan}</Badge></div>
            <div style={{ fontSize: 28, fontWeight: 750, margin: "10px 0 4px" }}>{u.minutes_used}<span className="muted" style={{ fontSize: 15, fontWeight: 500 }}> / {u.minutes_quota}</span></div>
            <div className="progress"><div style={{ width: `${pct}%` }} /></div>
          </div>
          <div className="card card-pad">
            <h3 style={{ fontSize: 15, marginBottom: 10 }}>Get started</h3>
            {steps.map((s) => (
              <div key={s.label} className="row between" style={{ padding: "8px 0", cursor: "pointer" }} onClick={() => nav(s.to)}>
                <div className="row"><div style={{ width: 26, height: 26, borderRadius: 8, display: "grid", placeItems: "center", background: s.done ? "var(--success-50)" : "var(--primary-50)", color: s.done ? "var(--success)" : "var(--primary)" }}><s.icon size={14} /></div>
                  <span style={{ textDecoration: s.done ? "line-through" : "none", color: s.done ? "var(--faint)" : "inherit" }}>{s.label}</span></div>
                <ArrowRight size={15} className="faint" />
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h3>Recent calls</h3><button className="btn sm" onClick={() => nav("/calls")}>View all</button></div>
        {d.recent?.length ? (
          <div className="table-wrap"><table className="table"><thead><tr><th>Agent</th><th>Direction</th><th>Number</th><th>Status</th><th>Duration</th><th>Sentiment</th><th>When</th></tr></thead>
            <tbody>{d.recent.map((c: any) => (
              <tr key={c.id} className="click" onClick={() => nav(`/calls?open=${c.id}`)}>
                <td><b>{c.agent_name || "-"}</b></td><td><Badge>{c.direction}</Badge></td>
                <td className="mono">{c.direction === "inbound" ? c.from_number : c.to_number}</td>
                <td><Badge>{c.status}</Badge></td><td>{fmtDur(c.duration_sec)}</td>
                <td>{c.sentiment ? <Badge>{c.sentiment}</Badge> : <Smile size={15} className="faint" />}</td><td className="muted">{ago(c.started_at)}</td>
              </tr>))}</tbody></table></div>
        ) : <Empty icon={<PhoneCall size={26} />} title="No calls yet" text="Test an agent in your browser or connect a number to get started." />}
      </div>
    </div>
  );
}
