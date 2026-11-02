import { useState, type FormEvent } from "react";

import { useAuth } from "../../app/AuthProvider";
import "./auth.css";

export function LoginScreen() {
  const { login } = useAuth();
  const [accountId, setAccountId] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(accountId.trim(), password);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <h1>Sentrix 回忆</h1>
        <p>请使用管理员分配给你的体验账号登录。</p>
        <label>
          账号
          <input
            autoComplete="username"
            value={accountId}
            onChange={(event) => setAccountId(event.target.value)}
            required
          />
        </label>
        <label>
          密码
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        {error !== "" && <p role="alert">{error}</p>}
        <button type="submit" disabled={busy || accountId.trim() === "" || password === ""}>
          {busy ? "正在登录…" : "登录"}
        </button>
      </form>
    </main>
  );
}
