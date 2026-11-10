import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api, getToken, setToken } from "./api";

export type User = {
  id: string; email: string; name: string; role: string; is_superadmin: boolean;
  tenant_id: string; tenant_name: string; plan: string; country: string;
};
type Ctx = {
  user: User | null; loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (d: any) => Promise<void>;
  logout: () => void; refresh: () => Promise<void>;
};
const AuthCtx = createContext<Ctx>(null as any);
export const useAuth = () => useContext(AuthCtx);
const RANK: Record<string, number> = { viewer: 0, member: 1, admin: 2, owner: 3 };
/** Mirrors the backend role checks: viewer < member < admin < owner. */
export const can = (user: User | null, role: "member" | "admin") => !!user && (user.is_superadmin || (RANK[user.role] ?? 0) >= RANK[role]);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    if (!getToken()) { setUser(null); setLoading(false); return; }
    try { setUser(await api("/api/auth/me")); } catch { setUser(null); }
    setLoading(false);
  };
  useEffect(() => { refresh(); }, []);

  const login = async (email: string, password: string) => {
    const r = await api("/api/auth/login", { body: { email, password } });
    setToken(r.token); setUser(r.user);
  };
  const signup = async (d: any) => {
    const r = await api("/api/auth/signup", { body: d });
    setToken(r.token); setUser(r.user);
  };
  const logout = () => { setToken(""); setUser(null); };
  return <AuthCtx.Provider value={{ user, loading, login, signup, logout, refresh }}>{children}</AuthCtx.Provider>;
}
