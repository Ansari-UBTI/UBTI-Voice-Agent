import { createContext, ReactNode, useCallback, useContext, useState } from "react";
import { CheckCircle2, Copy, Inbox, X, XCircle } from "lucide-react";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function Stat({ icon, label, value, tone }: { icon: ReactNode; label: string; value: ReactNode; tone?: string }) {
  const tones: Record<string, [string, string]> = {
    green: ["var(--success-50)", "var(--success)"], violet: ["#efedff", "var(--violet)"],
    amber: ["var(--warn-50)", "var(--warn)"], cyan: ["#e3fbff", "#0891b2"],
  };
  const [bg, fg] = tones[tone || ""] || ["var(--primary-50)", "var(--primary)"];
  return (
    <div className="card stat">
      <div className="ic" style={{ background: bg, color: fg }}>{icon}</div>
      <div className="v">{value}</div>
      <div className="l">{label}</div>
    </div>
  );
}

const TONE: Record<string, string> = {
  active: "green", completed: "green", ready: "green", running: "green", "in-progress": "green", positive: "green", done: "green", ok: "green",
  dialing: "amber", pending_review: "amber", processing: "amber", approved: "amber", paused: "amber", calling: "amber", neutral: "gray", draft: "gray", pending: "gray", trial: "gray",
  failed: "red", rejected: "red", suspended: "red", negative: "red", busy: "red", "no-answer": "red",
  inbound: "", outbound: "violet", test: "gray", enterprise: "violet", growth: "", starter: "green",
};
export function Badge({ children, tone, live }: { children: ReactNode; tone?: string; live?: boolean }) {
  const t = tone ?? TONE[String(children).toLowerCase()] ?? "";
  return <span className={`badge ${t}`}>{live && <span className="dot live" />}{String(children ?? "").replace(/_/g, " ")}</span>;
}

export function Modal({ open, onClose, title, children, footer, wide }: {
  open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  if (!open) return null;
  return (
    <div className="modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog">
        <div className="modal-head"><h3>{title}</h3><button className="btn ghost icon" onClick={onClose} aria-label="Close"><X size={18} /></button></div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export const Empty = ({ icon, title, text, action }: { icon?: ReactNode; title: string; text?: string; action?: ReactNode }) => (
  <div className="empty"><div className="ic">{icon || <Inbox size={26} />}</div><h3>{title}</h3>{text && <p>{text}</p>}{action && <div className="mt">{action}</div>}</div>
);
export const Loading = () => <div className="center"><div className="spinner" /></div>;
export const Toggle = ({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) => (
  <button type="button" className={`toggle ${on ? "on" : ""}`} onClick={() => onChange(!on)} aria-pressed={on} />
);
export const Field = ({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) => (
  <div className="field"><label>{label}</label>{children}{hint && <span className="hint">{hint}</span>}</div>
);
export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: string; icon?: ReactNode }[]; value: string; onChange: (v: string) => void }) {
  return <div className="tabs">{tabs.map((t) => <button key={t.id} className={value === t.id ? "on" : ""} onClick={() => onChange(t.id)}>{t.icon}{t.label}</button>)}</div>;
}
export function CopyBox({ text }: { text: string }) {
  const toast = useToast();
  return <div className="code"><span>{text}</span><button onClick={() => { navigator.clipboard.writeText(text); toast("Copied", "success"); }}><Copy size={13} /></button></div>;
}

type T = { id: number; msg: string; kind: string };
const ToastCtx = createContext<(m: string, k?: "success" | "error" | "info") => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<T[]>([]);
  const push = useCallback((msg: string, kind: string = "info") => {
    const id = Date.now() + Math.random();
    setItems((s) => [...s, { id, msg, kind }]);
    setTimeout(() => setItems((s) => s.filter((x) => x.id !== id)), 3800);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts">{items.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>{t.kind === "error" ? <XCircle size={18} /> : <CheckCircle2 size={18} />}{t.msg}</div>
      ))}</div>
    </ToastCtx.Provider>
  );
}
