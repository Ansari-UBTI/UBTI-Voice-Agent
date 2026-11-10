import { useEffect, useState } from "react";
import { History, RotateCcw, Upload } from "lucide-react";
import { api, fmtDate } from "../../api";
import { Badge, Empty, Modal, useToast } from "../ui";

export default function VersionsTab({ agent, onChanged, readOnly }: { agent: any; onChanged: (a: any) => void; readOnly?: boolean }) {
  const toast = useToast();
  const [list, setList] = useState<any[]>([]);
  const [view, setView] = useState<any>(null);
  const load = () => api(`/api/agents/${agent.id}/versions`).then(setList);
  useEffect(() => { load(); }, [agent.id, agent.published_version]);

  const restore = async (v: number, publish: boolean) => {
    if (!confirm(publish ? `Roll back live calls to v${v}?` : `Load v${v} into the draft? Unsaved draft changes are replaced.`)) return;
    try { const r = await api(`/api/agents/${agent.id}/versions/${v}/restore?publish_now=${publish}`, { method: "POST" }); onChanged(r.agent); load(); toast(publish ? `Live calls now use v${r.version}` : `v${v} loaded into draft`, "success"); }
    catch (e: any) { toast(e.message, "error"); }
  };

  return (
    <>
      <div className="muted">Live calls always use the <b>published</b> version. Edits are saved as a draft until you publish, and the browser test uses the draft. Roll back at any time.</div>
      {list.length ? (
        <div className="table-wrap"><table className="table"><thead><tr><th>Version</th><th>Note</th><th>By</th><th>When</th><th></th></tr></thead>
          <tbody>{list.map((v) => (
            <tr key={v.id}>
              <td><b>v{v.version}</b> {v.version === agent.published_version && <Badge tone="green">live</Badge>}</td>
              <td className="small">{v.note || "-"}</td><td className="small">{v.created_by_name || "-"}</td><td className="muted small">{fmtDate(v.created_at)}</td>
              <td><div className="row" style={{ justifyContent: "flex-end" }}>
                <button className="btn sm" onClick={() => api(`/api/agents/${agent.id}/versions/${v.version}`).then(setView)}>View</button>
                {!readOnly && <button className="btn sm" onClick={() => restore(v.version, false)}><RotateCcw size={13} />To draft</button>}
                {!readOnly && v.version !== agent.published_version && <button className="btn sm primary" onClick={() => restore(v.version, true)}><Upload size={13} />Roll back</button>}
              </div></td>
            </tr>))}</tbody></table></div>
      ) : <Empty icon={<History size={26} />} title="No versions yet" />}
      <Modal open={!!view} onClose={() => setView(null)} wide title={`Version ${view?.version}`}>
        <pre className="code" style={{ display: "block", whiteSpace: "pre-wrap", maxHeight: 480, overflow: "auto" }}>{view && JSON.stringify(view.config, null, 2)}</pre>
      </Modal>
    </>
  );
}
