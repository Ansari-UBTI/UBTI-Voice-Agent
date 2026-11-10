import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ArrowDownLeft, ArrowUpRight, Clock, PhoneCall, PhoneForwarded, TrendingUp, Wallet, XCircle } from "lucide-react";
import { api, fmtDur } from "../api";
import { Empty, Loading, PageHeader, Stat } from "../components/ui";

export const PIE = ["#0ea5e9", "#6d5ef5", "#12a37a", "#d98a07", "#e0475b", "#22d3ee"];
const SENT: Record<string, string> = { positive: "#12a37a", neutral: "#8aa0bb", negative: "#e0475b", unknown: "#cfdcea" };

export function Charts({ d }: { d: any }) {
  const box = (title: string, child: any, empty?: boolean) => (
    <div className="card"><div className="card-head"><h3>{title}</h3></div><div style={{ height: 270, padding: 12 }}>{empty ? <Empty title="No data yet" /> : <ResponsiveContainer>{child}</ResponsiveContainer>}</div></div>);
  return (<>
    <div className="grid g2">
      {box("Calls & minutes per day", <LineChart data={d.daily}><CartesianGrid stroke="#edf3fa" vertical={false} /><XAxis dataKey="day" tickFormatter={(v) => v.slice(5)} tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} width={34} /><Tooltip /><Legend />
        <Line type="monotone" dataKey="calls" stroke="#0e8fe0" strokeWidth={2.5} dot={false} /><Line type="monotone" dataKey="minutes" stroke="#6d5ef5" strokeWidth={2.5} dot={false} /></LineChart>, !d.daily?.length)}
      {box("Caller sentiment", <PieChart><Pie data={d.sentiment} dataKey="value" nameKey="name" innerRadius={60} outerRadius={95} paddingAngle={3}>
        {d.sentiment?.map((s: any, i: number) => <Cell key={i} fill={SENT[s.name] || PIE[i % 6]} />)}</Pie><Tooltip /><Legend /></PieChart>, !d.sentiment?.length)}
    </div>
    <div className="grid g3">
      {box("Calls by agent", <BarChart data={d.by_agent} layout="vertical"><XAxis type="number" hide /><YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 11 }} /><Tooltip /><Bar dataKey="calls" fill="#0ea5e9" radius={[0, 6, 6, 0]} /></BarChart>, !d.by_agent?.length)}
      {box("Top outcomes", <BarChart data={d.outcomes}><XAxis dataKey="name" tick={{ fontSize: 10 }} interval={0} /><YAxis width={30} tick={{ fontSize: 11 }} /><Tooltip /><Bar dataKey="value" fill="#6d5ef5" radius={[6, 6, 0, 0]} /></BarChart>, !d.outcomes?.length)}
      {box("Languages spoken", <PieChart><Pie data={d.languages} dataKey="value" nameKey="name" outerRadius={90}>{d.languages?.map((_: any, i: number) => <Cell key={i} fill={PIE[i % 6]} />)}</Pie><Tooltip /><Legend /></PieChart>, !d.languages?.length)}
    </div>
  </>);
}

export function Kpis({ k }: { k: any }) {
  return (<div className="grid g4">
    <Stat icon={<PhoneCall size={19} />} label="Total calls" value={k.total_calls || 0} />
    <Stat icon={<ArrowDownLeft size={19} />} label="Inbound" value={k.inbound || 0} tone="cyan" />
    <Stat icon={<ArrowUpRight size={19} />} label="Outbound" value={k.outbound || 0} tone="violet" />
    <Stat icon={<TrendingUp size={19} />} label="Success rate" value={`${k.success_rate || 0}%`} tone="green" />
    <Stat icon={<Clock size={19} />} label="Total minutes" value={Math.round((k.total_seconds || 0) / 60)} />
    <Stat icon={<Clock size={19} />} label="Avg duration" value={fmtDur(Math.round(k.avg_seconds || 0))} tone="cyan" />
    <Stat icon={<PhoneForwarded size={19} />} label="Transferred to human" value={k.transferred || 0} tone="amber" />
    <Stat icon={<Wallet size={19} />} label="Cost (USD)" value={`$${(k.cost_usd || 0).toFixed(2)}`} tone="violet" />
  </div>);
}

export default function Analytics() {
  const [days, setDays] = useState(30); const [d, setD] = useState<any>(null);
  useEffect(() => { setD(null); api(`/api/analytics/overview?days=${days}`).then(setD); }, [days]);
  return (
    <div className="stack">
      <PageHeader title="Analytics" subtitle="Performance of your voice agents"
        actions={<div className="seg">{[7, 30, 90].map((n) => <button key={n} className={days === n ? "on" : ""} onClick={() => setDays(n)}>{n} days</button>)}</div>} />
      {!d ? <Loading /> : <><Kpis k={d.kpis} /><Charts d={d} /></>}
    </div>
  );
}
