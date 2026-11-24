# 图片记忆构建 v3 实施计划（Master + S0）

> **For agentic workers:** 本文件是总路线图；每个切片开工前补一份同目录的详细任务单。S0 已展开为可执行任务。步骤用 `- [ ]` 跟踪。

**Goal:** 在现有 Asset → Observation → Event 图片链路上落地 v3：单图字段有来源与精度、命名前人物可用且不轻率报姓名、事件内容可追溯、用户可见可纠正、历史与导出可区分。

**Architecture:** 保留现有图片观察、人脸登记、事件归并、修正与清理机制；新增构建版本、字段证据、人物出现投影、真事件文档与导出快照。按阶段窄事务、依赖指纹和补偿任务协调，不引入全流水线 UnitOfWork 或独立素材记忆库。

**Tech Stack:** Python 3.11 API、SQLite/FTS5、现有向量后端、React/TypeScript/Vite 前端、独立 PhotoBench 包。开发主模型：`qwen3.8-flash-next-iq3_xxs` @ `http://127.0.0.1:8081/v1`。

---

## 0. 固定基线与环境

- 仓库：`/home/asus/Github/sentrix-image-memory-v3`（119）
- 基线：`8c209ebc980f04156629d82d69396ced7f12b4bd`，1634 个跟踪文件
- 分支：`feat/image-memory-v3`（GitHub 远端已建，指向基线）
- Python：3.11.16，`.venv` 复用 119 现有依赖后重装 editable 包
- 模型：`SENTRIX_VLM_BASE_URL=http://127.0.0.1:8081/v1`，`SENTRIX_VLM_MODEL=qwen3.8-flash-next-iq3_xxs`
- 基线单测结果：2 个既有失败（`test_portrait.py` 的 pending 显示名、`test_method_reachability.py` 的 `TurnRepository.list_for_conversation`），记录在案，不混入本计划改动。
- 119 直连 GitHub 可用；本机 Mac 有 `gh` 认证与 7890 代理。119 无 push 凭证，推送走 Mac 中继（见 §9）。

## 1. 不可变约束（摘自 v3 §1）

1. 本轮含后端、数据库、读取、图片搜索、现有前端、图片记忆导出、历史盘点/有界回填、更新删除、运行观测和验收材料；不以 HTTP 接口冒充用户能力完成。
2. 本轮不改 Agent 提示词、工具循环和回答逻辑；问答侧改善不验收、不承诺。
3. 图片视觉观察最长边不超过 1280；无局部补读、无双图默认输入、无额外人物描述轮、无模型自评轮。人脸检测沿用原输入策略。
4. 原事件归并的 6 小时窗、八分量、0.50 阈值、person_score 权重、split_guard、confirmed 评分桥保留；pending/描述/新可信档不反喂评分。
5. 不全量重跑模型，不自动重聚类历史库；普通命名、关系、描述修正、合并、拆分、撤回、删除、确定性回填均为 0 新增模型/检测/人脸嵌入调用。
6. 少量扩充观察/事件总结输出字段；事实选择、ID、坐标、来源、状态、权限由后端校验。
7. 开发者只做语法验证；功能/迁移/类型/架构/性能/真实模型验收由用户执行。本文件不报告这些检查已通过。

## 2. 切片路线图

| 切片 | 前置 | 迁移 | 主要文件/能力 | 完成条件 |
|---|---|---|---|---|
| S0 基础 | — | M1 0037 | 构建/证据/阶段任务表与端口；legacy 盘点；profile matrix；基线记录 | 依赖可装配；原生图片/关键帧盘点准确；legacy 状态可读；S0 单测通过 |
| S1 单图字段 | S0 | M2 0038 | observations 增 short/detail/tags/entities/field_states/有效 build；观察端口兼容；OCR 保护；媒体详情 | 新图详情可读 detail/tags/entities/精度/来源；旧图明确缺 detail；中文化不改 OCR |
| S2 人物出现 | S1 | M3 0039 | 检测前置、编号图、enroll 明细、质量隔离、可信档、描述 selector、单次纠错 | 未命名每张脸有稳定 ID/描述/框；低质量不膨胀成簇；候选不报姓名；原评分桥未混 pending |
| S3 人物检索/关系 | S2 | M3b 0040 | 出现 FTS、结构化 ID、观察语义组合、pending 关系、相似簇建议 | “灰色上衣”可定位到脸；local_only 可查；跨 scope 不漏 |
| S4 事件文档/时间线 | S1/S2 | M4 0041 | 事件参与者/时地/覆盖/总结证据；时间线展示投影 | 时间线可核对采集范围/活动时间、场景/地址、人物 ID、每条来源 |
| S5 事件索引 | S4 | M4 | event_document 空间、事件 FTS、成员展开、清理 | 事件命中可展开真实成员；新旧 event 不覆盖；单图事件额外嵌入=0 |
| S6 导出 | S1/S3/S4/S5 | M5 0042 | image_memories.jsonl + provenance + manifest；PhotoBench 适配 | compat/strict_v3 fixture 可被消费；字段状态/来源可核对 |
| S7 生命周期闭环 | S2–S6 | — | 命名/合并/拆分/reject/recluster/内部 merge/删除/迟到写入/raw 清理 | 各出口一致、无孤儿；普通操作零新增生成/检测调用 |
| S8 历史与灰度 | S1–S7 | — | H0 盘点、H1 确定性回填、可选 H2 重建；按 scope/profile 开关 | 新旧比例可见；H1 零模型；H2 只处理所选阶段 |
| S9 验收/交接 | S8 | — | 用户验收、profile/样本/预算归档、Agent schema 交接 | 22 项覆盖矩阵有证据；未通过 profile 不启用 |

每个切片开工前在 `docs/superpowers/plans/` 补详细任务单；不在本文件预留“稍后填写”的伪代码。

## 3. S0 详细任务

### Task 0.1 基线记录与 profile matrix

**Files:**
- Create: `docs/refactor/image-memory-v3-baseline.md`
- Create: `docs/refactor/image_profile_matrix.json`
- Create: `scripts/report_image_profile_matrix.py`

- [ ] **Step 1: 写基线记录**

内容固定记录：仓库路径、`git rev-parse HEAD`、`git ls-files | wc -l`、远端 main/target 分支 SHA、两个基线单测失败、119 模型端点与模型名（不含密钥）、`.venv` Python 版本、Pillow/ONNX 版本。任何后续切片引用此文件，不再重复口述。

- [ ] **Step 2: 写 profile matrix 生成脚本**

```python
# scripts/report_image_profile_matrix.py
from __future__ import annotations
import json
from dataclasses import asdict, dataclass
from sentrix.config import Settings

@dataclass(frozen=True)
class ImageProfile:
    deployment_profile: str
    llm_profile: str
    vlm_base_url: str
    vlm_model: str
    vlm_generation_profile: str
    vlm_context_tokens: int
    vlm_exact_output_budget: bool
    image_max_side: int | None
    embedding_scheme: str

def main() -> None:
    s = Settings()
    print(json.dumps(asdict(ImageProfile(
        deployment_profile=s.deployment_profile,
        llm_profile=s.llm_profile,
        vlm_base_url=s.vlm_base_url,
        vlm_model=s.vlm_model,
        vlm_generation_profile=s.vlm_generation_profile,
        vlm_context_tokens=s.vlm_context_tokens,
        vlm_exact_output_budget=s.vlm_exact_output_budget,
        image_max_side=None,  # S1 落地 image_observation_max_side 后改为实际值
        embedding_scheme=s.embedding_scheme,
    )), ensure_ascii=False, indent=2))
```

不记录 `api_key`。S1 增加 `image_observation_max_side` 后改为真实值。

- [ ] **Step 3: 生成并提交 matrix**

Run:
```bash
cd /home/asus/Github/sentrix-image-memory-v3
SENTRIX_VLM_BASE_URL=http://127.0.0.1:8081/v1 \
SENTRIX_VLM_MODEL=qwen3.8-flash-next-iq3_xxs \
SENTRIX_VLM_CONTEXT_TOKENS=131072 \
.venv/bin/python scripts/report_image_profile_matrix.py > docs/refactor/image_profile_matrix.json
```

Expected: JSON 含 `deployment_profile=server`、`llm_profile=local`、模型名与 131072。

### Task 0.2 M1 迁移

**Files:**
- Create: `apps/api/src/sentrix/infrastructure/persistence/migrations/0037_memory_build_foundation.sql`
- Test: `apps/api/tests/integration/test_memory_build_schema.py`

- [ ] **Step 1: 写失败测试**

```python
def test_m1_tables_and_keys(migrations_ready_db):
    with migrations_ready_db.connect() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"observation_builds", "memory_evidence", "memory_build_tasks"} <= tables
        cols = {r[1] for r in c.execute("PRAGMA table_info(observation_builds)")}
        assert {"build_key", "stage", "status", "input_hash", "model_name", "protocol_version"} <= cols
        uniq = {r[1] for r in c.execute("PRAGMA index_list(observation_builds)") if r[2]}
        assert "observation_builds_build_key" in uniq
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/tests/integration/test_memory_build_schema.py -q`
Expected: FAIL（表不存在）。

- [ ] **Step 3: 写迁移**

```sql
-- M1: 构建版本、字段证据、阶段续办。只加表，不改旧列。
CREATE TABLE IF NOT EXISTS observation_builds (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES memory_spaces(id),
    asset_id TEXT NOT NULL REFERENCES assets(id),
    observation_id TEXT REFERENCES observations(id),
    observation_revision INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    build_key TEXT NOT NULL,
    input_hash TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT '',
    protocol_version TEXT NOT NULL DEFAULT '',
    prompt_hash TEXT NOT NULL DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '{}',
    input_transform_json TEXT NOT NULL DEFAULT '{}',
    raw_response_ref TEXT NOT NULL DEFAULT '',
    error_category TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    attempt INTEGER NOT NULL DEFAULT 1,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(build_key)
);
CREATE INDEX IF NOT EXISTS idx_observation_builds_asset_stage
    ON observation_builds(asset_id, stage, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_observation_builds_scope_status
    ON observation_builds(scope_id, status);

CREATE TABLE IF NOT EXISTS memory_evidence (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES memory_spaces(id),
    owner_kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    field_path TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    value_json TEXT NOT NULL DEFAULT 'null',
    value_hash TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL,
    source_modality TEXT NOT NULL DEFAULT '',
    source_channel TEXT NOT NULL DEFAULT '',
    locator_json TEXT NOT NULL DEFAULT '{}',
    observation_revision INTEGER NOT NULL DEFAULT 0,
    build_id TEXT REFERENCES observation_builds(id),
    correction_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'effective',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_evidence_owner
    ON memory_evidence(scope_id, owner_kind, owner_id, field_path);
CREATE INDEX IF NOT EXISTS idx_memory_evidence_build ON memory_evidence(build_id);
CREATE INDEX IF NOT EXISTS idx_memory_evidence_status ON memory_evidence(status);

CREATE TABLE IF NOT EXISTS memory_build_tasks (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES memory_spaces(id),
    asset_id TEXT NOT NULL REFERENCES assets(id),
    stage TEXT NOT NULL,
    build_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    lease_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(build_key, stage)
);
CREATE INDEX IF NOT EXISTS idx_memory_build_tasks_due
    ON memory_build_tasks(status, next_attempt_at);
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest apps/api/tests/integration/test_memory_build_schema.py -q`
Expected: PASS。

### Task 0.3 Domain DTOs 与端口

**Files:**
- Modify: `apps/api/src/sentrix/domain/media/models.py`
- Modify: `apps/api/src/sentrix/domain/media/ports.py`
- Test: `apps/api/tests/unit/test_memory_build_models.py`

DTO 常量：
```python
BUILD_STAGES = ("metadata", "detection", "observation", "face_binding",
                "event_projection", "lexical_vector_index", "export_readiness")
BUILD_STATUSES = ("queued", "running", "succeeded", "partial", "failed")
EVIDENCE_OWNER_KINDS = ("asset", "observation", "event", "entity", "face_instance")
EVIDENCE_STATUSES = ("effective", "superseded", "retracted", "legacy", "conflict")
```

`ObservationBuild` 字段严格按 0037 列；`__post_init__` 校验 stage/status/attempt/revision。`MemoryEvidence` 字段严格按 0037 列；校验 owner_kind/status。`MemoryBuildTask` 字段严格按 0037 列。

端口：
```python
class MemoryBuildRepository(Protocol):
    def create_or_get(self, build: ObservationBuild) -> tuple[ObservationBuild, bool]: ...
    def get(self, build_id: str) -> ObservationBuild | None: ...
    def update_status(self, build_id: str, *, status: str, error_category: str = "",
                      error: str = "", finished_at: datetime | None = None) -> ObservationBuild | None: ...
    def list_for_asset(self, asset_id: str) -> Sequence[ObservationBuild]: ...
    def latest_for_stage(self, asset_id: str, stage: str) -> ObservationBuild | None: ...

class MemoryEvidenceRepository(Protocol):
    def add(self, evidence: MemoryEvidence) -> MemoryEvidence: ...
    def list_for_owner(self, *, scope_id: str, owner_kind: str, owner_id: str,
                       field_path: str = "", statuses: Sequence[str] = ()) -> Sequence[MemoryEvidence]: ...

class MemoryBuildTaskRepository(Protocol):
    def enqueue(self, task: MemoryBuildTask) -> MemoryBuildTask: ...
    def get(self, task_id: str) -> MemoryBuildTask | None: ...
    def list_due(self, *, limit: int) -> Sequence[MemoryBuildTask]: ...
```

### Task 0.4 SQLite 仓储

**Files:**
- Create: `apps/api/src/sentrix/infrastructure/persistence/repositories/memory_builds.py`
- Create: `apps/api/src/sentrix/infrastructure/persistence/repositories/memory_evidence.py`
- Test: `apps/api/tests/integration/test_memory_build_repository.py`
- Test: `apps/api/tests/integration/test_memory_evidence_repository.py`

实现要点：
- `create_or_get` 用 `INSERT ... ON CONFLICT(build_key) DO NOTHING`，再 `SELECT` 返回；`created` 由 `rowcount` 判断。
- `update_status` 用 `WHERE id=?`；不允许把 `succeeded` 回退成 `running`（返回 `None` 或抛 `ValueError`，按测试钉住）。
- `memory_evidence.add` 在写前校验 owner scope：`asset→assets.scope_id`、`observation→observations.scope_id`、`event→events.scope_id`、`entity→entities.scope_id`、`face_instance→face_instances→assets.scope_id`；不匹配抛 `ValueError("evidence owner scope mismatch")`。
- `build_id` 若写入，必须属于同一 asset/scope；否则 `ValueError`。
- 所有 JSON 列用 `json.dumps(..., ensure_ascii=False)`；时间用 `datetime.now(UTC).isoformat()`。
- 新 ID：`build_{uuid4().hex[:12]}`、`ev_{...}`、`task_{...}`。

### Task 0.5 生成式盘点用例与读取 API

**Files:**
- Create: `apps/api/src/sentrix/application/media/manage_memory_builds.py`
- Create: `apps/api/src/sentrix/api/schemas/memory_builds.py`
- Create: `apps/api/src/sentrix/api/routes/memory_builds.py`
- Modify: `apps/api/src/sentrix/bootstrap/container.py`
- Modify: `apps/api/src/sentrix/main.py`
- Test: `apps/api/tests/integration/test_manage_memory_builds.py`
- Test: `apps/api/tests/contract/test_memory_builds_routes.py`

用例 `ManageMemoryBuilds.inventory(scope_id, limit)`：
- 读 `AssetRepository.list_by_scope`，区分原生图片（`media_type=image and derived_kind in (None, "")`）与派生关键帧（`derived_kind` 或 `parent_asset_id` 非空）。
- 每资产读最新 observation 与 builds。
- 分类：
  - 无 build 且无有效语义字段 → `legacy`
  - 无 build 但有 caption/ocr/objects/facts 等 → `legacy_partial`
  - 最新 build `partial/failed` → `v3_partial`
  - 所有适用阶段 succeeded → `v3_complete`
- 返回含 `asset_id/file_name/media_kind/derived_kind/observation_id/observation_revision/available_fields/build_states/status`。
- 不调用模型。

API：
- `GET /api/v1/memory-builds?scope_id=...&limit=...` → `MemoryBuildInventoryResponse`
- `GET /api/v1/memory-builds/{build_id}` → `MemoryBuildResponse`
- 路由只做传输；用例在路由内用 container 仓储构造（与既有 identity 路由同体例）。
- `main.py` 的 `v1.include_router(...)` 列表加 `memory_builds.router`。

### Task 0.6 只读盘点脚本

**Files:**
- Create: `scripts/inventory_image_memory.py`

输出按 scope 的 JSON：原生图片数、关键帧数、有观察数、legacy/legacy_partial/v3_partial/v3_complete 数、缺 caption/OCR/人脸/时间的数量。只读，不调模型。

### Task 0.7 登记与生成物

- `docs/architecture/module-map.md`：新增 5 个 .py 条目与依赖边（用 `scripts/check_module_map.py` 校验）。
- `docs/refactor/capability-map.jsonl`：新增一条 `记忆构建版本与字段证据`，`origin_kind=NEW-2.0`，`status=verified`，`verification` 指向 `apps/api/tests/integration/test_memory_build_repository.py`。
- `packages/contracts/openapi/sentrix-v1.yaml`：`scripts/export_openapi.py` 重新生成（新增路由）。
- `docs/architecture/module-interfaces.md` 如新增端口，补端口说明。

### Task 0.8 S0 验证与提交

```bash
cd /home/asus/Github/sentrix-image-memory-v3
.venv/bin/python -m pytest apps/api/tests/unit/test_memory_build_models.py \
  apps/api/tests/integration/test_memory_build_schema.py \
  apps/api/tests/integration/test_memory_build_repository.py \
  apps/api/tests/integration/test_memory_evidence_repository.py \
  apps/api/tests/integration/test_manage_memory_builds.py \
  apps/api/tests/contract/test_memory_builds_routes.py -q
.venv/bin/python -m ruff check <changed files>
.venv/bin/python -m mypy <changed files>  # 可选；用户最终跑 make typecheck
```

提交信息：`feat(memory): add build and evidence foundation (S0)`。

## 4. S1–S9 展开时机

每个切片开工前，在 `docs/superpowers/plans/` 写同结构的详细任务单（Files / TDD steps / exact commands），不在 Master 里预埋伪代码。S1 的第一件事是 M2 与观察端口兼容；S2 的第一件事是编号图与 enroll 明细；S3 是出现 FTS；S4 是事件文档；S5 是事件索引；S6 是导出；S7 是生命周期；S8 是历史；S9 是验收。

## 5. 用户验收/门禁边界

开发者只跑：新增/相关的 unit/integration/contract 用例、ruff、AST/语法检查。用户执行：`make typecheck`、`make arch`、`make check-docs`、`make test-contract`、`make test-integration`、`make web-check`、`make photobench-check`、`make ci`，以及真实模型金标。未运行的检查在交付记录中写“未执行”。

## 6. 已知基线失败

- `test_portrait.py::test_pack_person_block_uses_confirmed_values_only`：基线代码返回“未确认姓名人物”，测试期望内部簇名。S2 会重新定义 pending 显示标签，届时一并裁决。
- `test_method_reachability.py::test_real_repository_has_no_problems`：`TurnRepository.list_for_conversation` 无生产调用者。与图片计划无关，保持记录，不顺手改。

## 7. 推送中继

119 无 GitHub push 凭证。提交后：
1. 119：`git bundle create /tmp/image-memory-v3.bundle feat/image-memory-v3`
2. Mac：`scp` 取回 bundle。
3. Mac 上已有 `baseline-v2`（shallow）。若 shallow 直接 push 失败，先在 Mac 建一个非 shallow 的临时 clone 或 `git fetch --unshallow`，再从 bundle `git fetch` 后 push 到 `origin feat/image-memory-v3`。
4. 每次推送记录 SHA；不 force push。
