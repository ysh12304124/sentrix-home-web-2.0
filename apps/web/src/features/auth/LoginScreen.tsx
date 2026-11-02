import { useState, type FormEvent } from "react";

import { useAuth } from "../../app/AuthProvider";

export function LoginScreen({ adminOnly = false }: { adminOnly?: boolean }) {
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
      <section className="login-story">
        <div className="login-brand">
          <span>S</span>
          <strong>{adminOnly ? "Sentrix Admin" : "Sentrix Memory"}</strong>
        </div>
        <div>
          <h1>{adminOnly ? "看住每一个服务，也看住每一次恢复。" : "让家里的每一天，自己讲成故事。"}</h1>
          <p>
            {adminOnly
              ? "独立管理入口：服务状态、机器资源、模型队列、告警、作业与用户反馈。"
              : "照片、视频和问答都在你自己的空间里。导入后系统会离线整理事件、人物与回忆，你只需要打开首页，慢慢看。"}
          </p>
          <ul>
            <li>每个人独立的数据空间与账号</li>
            <li>上传后自动处理，关掉页面也不会中断</li>
            <li>视频先显示封面，点击才加载原片</li>
          </ul>
        </div>
        <p style={{ fontSize: 12, color: "rgba(244,243,234,.45)" }}>
          局域网体验版 · 账号由管理员预先创建
        </p>
      </section>
      <div className="login-form-wrap">
        <form className="login-card" onSubmit={submit}>
          <h2>{adminOnly ? "管理员登录" : "欢迎回来"}</h2>
          <p className="login-sub">
            {adminOnly ? "此入口只接受管理员账号，与用户入口会话互不影响。" : "使用管理员分配给你的体验账号登录。"}
          </p>
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
            {busy ? "正在登录…" : adminOnly ? "进入管理台" : "进入我的回忆"}
          </button>
        </form>
      </div>
    </main>
  );
}
