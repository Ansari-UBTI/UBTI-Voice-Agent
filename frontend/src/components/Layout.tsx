import { useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  AudioLines, BarChart3, BookOpen, Bot, Building2, Code2, LayoutDashboard, LogOut, Megaphone, Menu, Mic2,
  PhoneCall, Phone, ScrollText, Settings, Shield, ShieldCheck, Activity, Hash, Radio, Eye,
} from "lucide-react";
import { can, useAuth } from "../auth";

const NAV = [
  { section: "Build", items: [
    { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
    { to: "/agents", label: "Agents", icon: Bot },
    { to: "/voices", label: "Voices", icon: Mic2 },
    { to: "/knowledge", label: "Knowledge Base", icon: BookOpen },
  ]},
  { section: "Deploy", items: [
    { to: "/numbers", label: "Phone Numbers", icon: Phone },
    { to: "/campaigns", label: "Batch Calls", icon: Megaphone },
  ]},
  { section: "Monitor", items: [
    { to: "/live", label: "Live Calls", icon: Radio },
    { to: "/calls", label: "Call History", icon: PhoneCall },
    { to: "/analytics", label: "Analytics", icon: BarChart3 },
  ]},
  { section: "Configure", items: [
    { to: "/developers", label: "Developers", icon: Code2 },
    { to: "/settings", label: "Settings", icon: Settings },
  ]},
];
const ADMIN = [
  { to: "/admin", label: "Platform Overview", icon: Shield, end: true },
  { to: "/admin/tenants", label: "Tenants", icon: Building2 },
  { to: "/admin/calls", label: "All Calls", icon: Activity },
  { to: "/admin/numbers", label: "All Numbers", icon: Hash },
  { to: "/admin/voices", label: "Voice Approvals", icon: ShieldCheck },
  { to: "/admin/audit", label: "Audit & Health", icon: ScrollText },
];

export default function Layout() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState(false);
  const nav = useNavigate();
  const loc = useLocation();
  const initials = (user?.name || "U").split(" ").map((s) => s[0]).slice(0, 2).join("").toUpperCase();
  const title = [...NAV.flatMap((s) => s.items), ...ADMIN].find((i) => (i.end ? loc.pathname === i.to : loc.pathname.startsWith(i.to) && i.to !== "/"))?.label || "Dashboard";

  return (
    <div className="app">
      <div className={`overlay ${open ? "open" : ""}`} onClick={() => setOpen(false)} />
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="brand">
          <div className="brand-logo"><AudioLines size={20} /></div>
          <div><b>UBTI Voice Agent</b><small>{user?.tenant_name}</small></div>
        </div>
        <nav className="nav" onClick={() => setOpen(false)}>
          {NAV.map((s) => (
            <div key={s.section}>
              <div className="nav-section">{s.section}</div>
              {s.items.map((i) => <NavLink key={i.to} to={i.to} end={i.end}><i.icon size={17} />{i.label}</NavLink>)}
            </div>
          ))}
          {user?.is_superadmin && (
            <div>
              <div className="nav-section">UBTI Admin</div>
              {ADMIN.map((i) => <NavLink key={i.to} to={i.to} end={i.end} className="admin"><i.icon size={17} />{i.label}</NavLink>)}
            </div>
          )}
        </nav>
        <div className="side-foot">
          <div className="usage-box">
            <div className="row between small"><b style={{ textTransform: "capitalize" }}>{user?.plan} plan</b><span className="badge">{user?.country}</span></div>
            <div className="small muted" style={{ marginTop: 4 }}>Upgrade for more minutes & numbers</div>
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="btn ghost icon menu-btn" onClick={() => setOpen(true)} aria-label="Menu"><Menu size={20} /></button>
          <b className="hide-sm" style={{ fontSize: 15 }}>{title}</b>
          <div className="grow" />
          <button className="btn sm hide-sm" onClick={() => nav("/agents")}><Bot size={15} />New agent</button>
          <div className="user-chip" onClick={() => setMenu(!menu)}>
            <div className="avatar">{initials}</div>
            <div className="hide-sm" style={{ lineHeight: 1.2, paddingRight: 6 }}><b style={{ fontSize: 13 }}>{user?.name}</b><div className="small muted">{user?.role}</div></div>
            {menu && (
              <div className="dropdown" onMouseLeave={() => setMenu(false)}>
                <div className="small muted" style={{ padding: "6px 10px" }}>{user?.email}</div>
                <button onClick={() => nav("/settings")}><Settings size={15} />Settings</button>
                <button onClick={() => { logout(); nav("/login"); }}><LogOut size={15} />Sign out</button>
              </div>
            )}
          </div>
        </header>
        <main className="content">
          {!can(user, "member") && <div className="notice"><Eye size={15} />You have view-only access. Ask an admin to change your role to make changes.</div>}
          <Outlet />
        </main>
      </div>
    </div>
  );
}
