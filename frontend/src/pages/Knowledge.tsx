import { useEffect, useRef, useState } from "react";
import { BookOpen, FileText, Globe, Plus, RefreshCw, Trash2, Type, Upload } from "lucide-react";
import { api, ago } from "../api";
import { Badge, Empty, Field, Loading, Modal, PageHeader, Tabs, useToast } from "../components/ui";

export default function Knowledge() {
  const toast = useToast();
  const [docs, setDocs] = useState<any[] | null>(null); const [agents, setAgents] = useState<any[]>([]);
  const [open, setOpen] = useState(false); const [mode, setMode] = useState("file");
  const [agentId, setAgentId] = useState(""); const [name, setName] = useState(""); const [text, setText] = useState(""); const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null); const [busy, setBusy] = useState(false); const [view, setView] = useState<any>(null);
  const fi = useRef<HTMLInputElement>(null);
  const [st, setSt] = useState<any>(null);
  const load = () => { api("/api/knowledge").then(setDocs); api("/api/knowledge/status").then(setSt); };
  useEffect(() => { load(); api("/api/agents").then(setAgents); }, []);

  const add = async () => {
    setBusy(true);
    try {
      if (mode === "file") { const fd = new FormData(); fd.append("file", file!); fd.append("agent_id", agentId); await api("/api/knowledge/file", { form: fd }); }
      else if (mode === "url") await api("/api/knowledge/url", { body: { url, agent_id: agentId || null } });
      else await api("/api/knowledge/text", { body: { name, content: text, agent_id: agentId || null } });
      toast("Added to knowledge base", "success"); setOpen(false); setFile(null); setText(""); setUrl(""); setName(""); load();
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  if (!docs) return <Loading />;
  const icon = (t: string) => t === "url" ? <Globe size={16} /> : t === "text" ? <Type size={16} /> : <FileText size={16} />;

  return (
    <>
      <PageHeader title="Knowledge Base" subtitle="Give your agents facts: FAQs, price lists, policies, brochures, website pages"
        actions={<><button className="btn" title="Rebuild the search index (e.g. after enabling embeddings)" onClick={async () => { try { const r = await api("/api/knowledge/reindex", { method: "POST" }); toast(`Re-indexed ${r.documents} documents`, "success"); load(); } catch (e: any) { toast(e.message, "error"); } }}><RefreshCw size={15} />Re-index</button><button className="btn primary" onClick={() => setOpen(true)}><Plus size={16} />Add document</button></>} />
      {st && <div className="card card-pad mb small" style={{ boxShadow: "none" }}>
        <b>How agents use this:</b> up to {(st.inline_limit / 1000).toFixed(0)}k characters per agent are given to the agent in full (fastest). Larger knowledge bases are split into passages and the agent <b>searches</b> them during the call, so nothing is cut off.
        Search mode: <Badge tone={st.embeddings ? "green" : "gray"}>{st.embeddings ? "semantic (Azure OpenAI embeddings)" : "keyword"}</Badge> · Total: {(st.total_chars / 1000).toFixed(1)}k characters
      </div>}
      <div className="card">
        {docs.length ? (
          <div className="table-wrap"><table className="table"><thead><tr><th>Name</th><th>Type</th><th>Size</th><th>Used by</th><th>Added</th><th></th></tr></thead>
            <tbody>{docs.map((d) => (
              <tr key={d.id} className="click" onClick={() => api(`/api/knowledge/${d.id}`).then(setView)}>
                <td><div className="row"><span style={{ color: "var(--primary)" }}>{icon(d.source_type)}</span><b style={{ wordBreak: "break-all" }}>{d.name}</b></div></td>
                <td><Badge tone="gray">{d.source_type}</Badge></td><td className="muted">{(d.chars / 1000).toFixed(1)}k chars{d.chunks ? <div className="small faint">{d.chunks} passages{d.embedded ? " · embedded" : ""}</div> : null}</td>
                <td>{d.agent_name ? <Badge>{d.agent_name}</Badge> : <Badge tone="green">All agents</Badge>}</td><td className="muted">{ago(d.created_at)}</td>
                <td><div className="row">{d.source_type === "url" && <button className="btn icon sm" title="Re-fetch page" onClick={async (e) => { e.stopPropagation(); try { await api(`/api/knowledge/${d.id}/refresh`, { method: "POST" }); toast("Page refreshed", "success"); load(); } catch (er: any) { toast(er.message, "error"); } }}><RefreshCw size={14} /></button>}<button className="btn icon sm danger" onClick={async (e) => { e.stopPropagation(); if (confirm("Delete document?")) { await api(`/api/knowledge/${d.id}`, { method: "DELETE" }); load(); } }}><Trash2 size={14} /></button></div></td>
              </tr>))}</tbody></table></div>
        ) : <Empty icon={<BookOpen size={26} />} title="Your knowledge base is empty" text="Upload a PDF, paste FAQs, or import a web page." action={<button className="btn primary" onClick={() => setOpen(true)}><Plus size={16} />Add document</button>} />}
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title="Add to knowledge base"
        footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button><button className="btn primary" disabled={busy || (mode === "file" ? !file : mode === "url" ? !url : !(name && text))} onClick={add}>{busy ? "Processing..." : "Add"}</button></>}>
        <Tabs value={mode} onChange={setMode} tabs={[{ id: "file", label: "Upload file", icon: <Upload size={14} /> }, { id: "url", label: "Website URL", icon: <Globe size={14} /> }, { id: "text", label: "Paste text", icon: <Type size={14} /> }]} />
        {mode === "file" && <div className="drop" onClick={() => fi.current?.click()}><Upload size={22} /><div style={{ marginTop: 6 }}>{file ? <b>{file.name}</b> : "Click to choose PDF, DOCX, TXT, MD or CSV"}</div>
          <input ref={fi} type="file" hidden accept=".pdf,.docx,.txt,.md,.csv" onChange={(e) => setFile(e.target.files?.[0] || null)} /></div>}
        {mode === "url" && <Field label="Page URL"><input className="input" placeholder="https://yourcompany.com/faq" value={url} onChange={(e) => setUrl(e.target.value)} /></Field>}
        {mode === "text" && <><Field label="Title"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Pricing & offers" /></Field>
          <Field label="Content"><textarea className="input" style={{ minHeight: 180 }} value={text} onChange={(e) => setText(e.target.value)} /></Field></>}
        <Field label="Available to"><select className="input" value={agentId} onChange={(e) => setAgentId(e.target.value)}><option value="">All agents</option>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
      </Modal>
      <Modal open={!!view} onClose={() => setView(null)} title={view?.name} wide>
        <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit", lineHeight: 1.6 }}>{view?.content?.slice(0, 20000)}</pre>
      </Modal>
    </>
  );
}
