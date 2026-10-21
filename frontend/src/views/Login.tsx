import { useState } from "react";
import { api } from "../lib/api";
import { useApp } from "../state";
import { Logo } from "../components/ui";

export function Login({ setup }: { setup: boolean }) {
  const { refreshAuth } = useApp();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(setup ? "/hub/api/auth/setup" : "/hub/api/auth/login", { body: { username, password } });
      await refreshAuth();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <main className="signin">
      <form onSubmit={submit}>
        <h1><Logo /> SLQ Hub</h1>
        {setup && <p className="muted" style={{ margin: 0 }}>No account exists yet. Choose the admin's name and password.</p>}
        <label className="field"><span>User name</span>
          <input className="input" autoComplete="username" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} /></label>
        <label className="field"><span>Password</span>
          <input className="input" type="password" autoComplete={setup ? "new-password" : "current-password"} value={password}
            onChange={(e) => setPassword(e.target.value)} /></label>
        {error && <div className="callout fault" role="alert">{error}</div>}
        <button className="btn primary" disabled={busy || !username || !password}>{setup ? "Create the account" : "Sign in"}</button>
      </form>
    </main>
  );
}
