import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as Icons from "lucide-react";
import { Bot, Copy, Plus, Search, Sparkles, Trash2 } from "lucide-react";
import { api, ago } from "../api";
import { Badge, Empty, Loading, Modal, PageHeader, useToast } from "../components/ui";

export default function Agents() {
  const nav = useNavigate(); const toast = useToast();
  const [list, setList] = useState<any[] | null>(null);
  const [meta, setMeta] = useState<any>({ templates: [] });
  const [open, setOpen] = useState(false); const [tpl, setTpl] = useState("blank"); const [name, setName] = useState("");
  const [q, setQ] = useState("");
  const load = () => api("/api/agents").then(setList);
  useEffect(() => { load(); api("/api/agents/meta").then(setMeta); }, []);

  const create = async () => {
    try {
      const a = await api(`/api/agents${tpl !== "blank" ? `?template=${tpl}` : ""}`, { body: { name: name || undefined } });
      toast("Agent created", "success"); nav(`/agents/${a.id}`);
    } catch (e: any) { toast(e.message, "error"); }
  };
  const dup = async (e: any, id: string) => { e.stopPropagation(); await api(`/api/agents/${id}/duplicate`, { method: "POST" }); toast("Duplicated", "success"); load(); };
  const del = async (e: any, id: string) => { e.stopPropagation(); if (!confirm("Delete this agent?")) return; await api(`/api/agents/${id}`, { method: "DELETE" }); load(); };
  if (!list) return <Loading />;
  const shown = list.filter((a) => a.name.toLowerCase().includes(q.toLowerCase()));

  return (
    <>
      <PageHeader title="Agents" subtitle="Create, configure and test your AI voice agents"
        actions={<><div className="search"><Search size={15} /><input className="input" placeholder="Search agents" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <button className="btn primary" onClick={() => setOpen(true)}><Plus size={16} />Create agent</button></>} />
      {shown.length ? (
        <div className="grid g3">
          {shown.map((a) => (
            <div key={a.id} className="card agent-card" onClick={() => nav(`/agents/${a.id}`)}>
              <div className="row between">
                <div className="row"><div className="agent-ava"><Bot size={21} /></div><div><b style={{ fontSize: 15 }}>{a.name}</b><div className="small muted">Updated {ago(a.updated_at)}</div></div></div>
                <Badge>{a.status}</Badge>
              </div>
              <div className="muted small" style={{ minHeight: 38, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{a.first_message}</div>
              <div className="row wrap" style={{ gap: 6 }}>
                <Badge tone="gray">{a.language === "auto" ? "Multilingual" : a.language}</Badge>
                <Badge tone="violet">{a.voice_type === "azure-personal" ? "Cloned voice" : (a.voice_name || "").split("-").pop()?.replace("Neural", "")}</Badge>
                <Badge tone="">{a.model}</Badge>
              </div>
              <div className="row between" style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
                <span className="small muted">{a.total_calls} calls · {a.numbers} numbers</span>
                <div className="row" style={{ gap: 2 }}>
                  <button className="btn ghost icon" title="Duplicate" onClick={(e) => dup(e, a.id)}><Copy size={15} /></button>
                  <button className="btn ghost icon danger" title="Delete" onClick={(e) => del(e, a.id)}><Trash2 size={15} /></button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="card"><Empty icon={<Bot size={26} />} title="No agents yet" text="Start from a ready-made template or a blank agent." action={<button className="btn primary" onClick={() => setOpen(true)}><Plus size={16} />Create agent</button>} /></div>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="Create a new agent" wide
        footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button><button className="btn primary" onClick={create}><Sparkles size={15} />Create agent</button></>}>
        <input className="input" placeholder="Agent name (e.g. Priya - Front Desk)" value={name} onChange={(e) => setName(e.target.value)} />
        <div className="grid g3">
          <div className={`tpl ${tpl === "blank" ? "on" : ""}`} onClick={() => setTpl("blank")}><Plus size={20} color="var(--primary)" /><b style={{ display: "block", marginTop: 8 }}>Blank agent</b><span className="small muted">Start from scratch</span></div>
          {meta.templates.map((t: any) => {
            const I = (Icons as any)[t.icon] || Bot;
            return <div key={t.id} className={`tpl ${tpl === t.id ? "on" : ""}`} onClick={() => setTpl(t.id)}><I size={20} color="var(--primary)" /><b style={{ display: "block", marginTop: 8 }}>{t.name}</b><span className="small muted" style={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{t.first_message}</span></div>;
          })}
        </div>
      </Modal>
    </>
  );
}
