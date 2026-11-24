# 图片记忆 v3 基线记录

记录日期：2026-11-24
实施仓库：`/home/asus/Github/sentrix-image-memory-v3`（192.168.1.119）
分支：`feat/image-memory-v3`
基线 commit：`8c209ebc980f04156629d82d69396ced7f12b4bd`
跟踪文件数：1634
远端 `main`：`8c209ebc`（与本基线一致）
远端工作分支：`feat/image-memory-v3`（指向同一 commit）

## 运行环境

- Python：3.11.16（`.venv` 复用 119 既有依赖后重装 `apps/api` / `apps/photobench` editable 包）
- 开发主模型（2026-11-24 变更）：云端 `qwen3.8-flash`，OpenAI 兼容端点
  `https://llm-355ba48p9iw7pkjz.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`
- 云端配置：`/home/asus/sentrix-image-memory-cloud.json`（无密钥）；
  密钥只放在权限 600 的 `/home/asus/sentrix-image-memory.env`，不进入仓库
- 模型能力：文本 + 图片；实测图片 + `response_format={"type":"json_object"}` 返回合法 JSON，
  `OpenAIVlmObserver` 走该配置可返回 caption/ocr_text
- 其他侧车仍在 119：WeMM 嵌入 `127.0.0.1:8102`、MOSS ASR `8106`、SenseVoice `8107`
- 本地 Qwen3.8 `127.0.0.1:8081` 只保留作对照，不再是应用主模型

## 基线自动测试

`unit` 用例已跑，2 个既有失败，不是本计划改动：

1. `apps/api/tests/unit/test_portrait.py::test_pack_person_block_uses_confirmed_values_only`
   - 测试期望 pending 的 `display_name` 为内部名“待确认人物簇 · cluster-1”
   - 当前代码返回“未确认姓名人物”（`entity_display_name` 的遮蔽规则）
   - S2 重新定义 pending 显示标签时一并裁决
2. `apps/api/tests/unit/test_method_reachability.py::test_real_repository_has_no_problems`
   - `TurnRepository.list_for_conversation` 无生产调用者
   - 与图片计划无关，保持记录，不顺手改

## 配置矩阵

`docs/refactor/image_profile_matrix.json` 由 `scripts/report_image_profile_matrix.py` 生成。生成命令：

```bash
set -a && . /home/asus/sentrix-image-memory.env && set +a
.venv/bin/python scripts/report_image_profile_matrix.py \
  > docs/refactor/image_profile_matrix.json
```

矩阵不包含 API key；模型名与端点只作为开发基线，不作为部署契约。

## 推送中继

119 无 GitHub push 凭证。提交后从 119 生成 bundle，经 Mac 的 `gh` 认证与 7890 代理推送；不使用 force push。
