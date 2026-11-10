import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import { Loading } from "./components/ui";
import { Login, Signup } from "./pages/Auth";
import Dashboard from "./pages/Dashboard";
import Agents from "./pages/Agents";
import AgentEditor from "./pages/AgentEditor";
import Voices from "./pages/Voices";
import Knowledge from "./pages/Knowledge";
import Numbers from "./pages/Numbers";
import Campaigns from "./pages/Campaigns";
import Calls from "./pages/Calls";
import Live from "./pages/Live";
import Analytics from "./pages/Analytics";
import Developers from "./pages/Developers";
import SettingsPage from "./pages/Settings";
import { AdminAudit, AdminCalls, AdminNumbers, AdminOverview, AdminTenants, AdminVoices } from "./pages/admin/Admin";

export default function App() {
  const { user, loading } = useAuth();
  if (loading) return <Loading />;
  if (!user)
    return (
      <Routes>
        <Route path="/signup" element={<Signup />} />
        <Route path="*" element={<Login />} />
      </Routes>
    );
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="agents" element={<Agents />} />
        <Route path="agents/:id" element={<AgentEditor />} />
        <Route path="voices" element={<Voices />} />
        <Route path="knowledge" element={<Knowledge />} />
        <Route path="numbers" element={<Numbers />} />
        <Route path="campaigns" element={<Campaigns />} />
        <Route path="calls" element={<Calls />} />
        <Route path="live" element={<Live />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="developers" element={<Developers />} />
        <Route path="settings" element={<SettingsPage />} />
        {user.is_superadmin && (
          <>
            <Route path="admin" element={<AdminOverview />} />
            <Route path="admin/tenants" element={<AdminTenants />} />
            <Route path="admin/calls" element={<AdminCalls />} />
            <Route path="admin/numbers" element={<AdminNumbers />} />
            <Route path="admin/voices" element={<AdminVoices />} />
            <Route path="admin/audit" element={<AdminAudit />} />
          </>
        )}
        <Route path="*" element={<Navigate to="/" />} />
      </Route>
    </Routes>
  );
}
