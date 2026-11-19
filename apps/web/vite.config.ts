import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import mkcert from "vite-plugin-mkcert";

// defineConfig 从 vitest/config 取：它就是把 vite 那份原样加上 ``test`` 字段，
// 所以 plugins / server 仍然是同一份配置，测试跑的 JSX 转换与构建跑的完全一致。
const previewTarget = process.env.SENTRIX_PREVIEW_TARGET ?? "http://127.0.0.1:8210";

export default defineConfig(() => {
  return {
    // Vitest also uses this config; tests must not install certificates.
    plugins: [react(), ...(process.env.VITEST ? [] : [mkcert()])],
    server: {
      host: true,
      port: 4274,
      strictPort: true,
      fs: { deny: [".env", ".env.*", "*.{crt,pem}", "**/.git/**", "**/.certs/**"] },
      proxy: {
        "/api": { target: previewTarget, changeOrigin: true },
        "/control": { target: previewTarget, changeOrigin: true },
      },
    },
    test: {
      environment: "jsdom",
      include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    },
  };
});
