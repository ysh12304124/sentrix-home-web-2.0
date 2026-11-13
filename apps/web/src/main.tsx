import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import "./styles/tokens.css";
import "./styles/shell.css";
import "./styles/components.css";
import "./styles/family.css";
// 旧的 styles.css 暂时保留：Timeline / PersonInsights / AssistantPanel / AssetGrid /
// SearchBox 五个组件还在用它里面的类名，F2–F5 各自迁走时一并删除。
import "./styles.css";
// 体验入口的视觉层：只覆盖壳层、登录、导入、管理与反馈；旧页面结构不动。
import "./styles/experience.css";
import "./styles/themes.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
