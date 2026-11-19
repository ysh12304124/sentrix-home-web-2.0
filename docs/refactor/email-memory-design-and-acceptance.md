# ATM-Bench 邮件记忆：设计与验收

> 本文记录邮件接入的上一阶段。2026-11-18已统一为文本记忆，当前实现和验收见
> [统一文本记忆](unified-text-memory-acceptance.md)；不再维护独立邮件记忆处理流程。

## 1. 结论和边界

邮件作为文本来源进入现有统一记忆库，不转成图片，不另建邮件专用图。
图中的 Observation、时间、地点不增加独立节点。此次没有更换推理模型、启用效用评分，
也没有写入现有 PhotoBench 正式相册。验收数据与正式数据隔离。

**功能链路通过不代表 ATM-Bench 问答准确率 100%，也不代表整个仓库 CI 已通过。**
完整数据导入、确定性关联及修正删除测试，与真实模型答案质量分开记录。

## 2. 权威数据模型

| 层次 | 邮件的落点 | 主要字段 |
| --- | --- | --- |
| Asset | `media_type=text` | scope、稳定资产 ID、原邮件 ID.txt、原正文文件路径、源内容指纹、记录时间 |
| Observation | `source_type=email` | `external_id`、`caption` 摘要、`text_content` 正文、revision |
| Event | 一封邮件一个通信事件 | `event_type=email`、source_asset_id、title、summary、time_start=time_end |
| 导入状态 | `email_import_state` | asset/observation/event 外键、indexed_revision、last_error |
| 图投影 | Asset 和 Event 节点 | Observation 数据嵌入 Asset 属性，来源 ID 和 revision 保留 |

数据库迁移：`0032_email_memory.sql`。旧观察的新增字段默认为空，不修改既有图片数据。
邮件 ID 在同一个 scope 唯一；不同相册允许拥有同一封源邮件。

邮件时间保存数据集原值及其时区状态，不把无时区时间擅自解释成 UTC。
正文中的预订日期是引用活动日期，与通信事件时间区分。

## 3. 入库、索引和失败恢复

1. 解析原始 `emails.json` 中 id、timestamp、short_summary、detail；验证类型、长度、时间、ID。
2. 校验一批内重复 ID、已有源内容冲突。相同内容重复导入跳过，不覆盖人工修正。
3. 原正文落文件；Asset、Observation、Event、事件成员、导入状态在一个数据库事务内写入。
4. 摘要 + 正文进入现有关键词索引及 BGE-M3 文本向量。
5. 沿用现有 semantic/episodic 存储：每封邮件三条文本向量记录；不生成视觉向量。
6. 索引失败返回 partial，权威记忆保留、错误可见；重传同一文件重试未完成 revision。
7. 批量导入结束构图一次。产品页面每批 50 封，只有最后一批重建图。

正文来源只读 `data/raw_memory/email/emails.json`，导入器不读取 QA、答案、解释或 GT。
发布版邮件已脱敏，不额外调用大模型改写，以免改变测评事实。

存储文件与 SQL 不属于同一个跨资源事务；异常崩溃可能留下未被 SQL 引用的文件。
API 在进程内串行化导入/修正，批量 CLI 应由单个管理员执行，不应与另一导入进程并发写同一 scope。

## 4. 图结构与跨模态路径

基础边为事件成员/来源关系，沿用既有图合同。
本次新增的跨事件边只面向正文有显式预订、预约语义且能解析明确日期的场景：

- `email_refers_to_event`：正文提及活动日期，并且地点名称能与目标事件属性匹配；推断上下文边。
- `email_time_candidate`：只有活动日期匹配；低置信候选边。

每条边保留邮件 external_id、observation_id、revision、来源摘录、引用日期、地点提及、
关联原因和证据级别。边数有上限，每封最多关联 24 个活动事件。
按活动日期预分组，避免全邮件两两比较。

例如：

```text
邮件 Asset → 邮件通信 Event（2月11日）
              ↓ email_time_candidate：正文提及2月21日
           照片 Event（2月21日） → 照片 Asset
```

邮件之间不因“时间接近”批量建立时序边。预约上下文不等于活动已经发生、已付款、已到店，
更不自动变成因果边。照片没有餐厅确认时保留弱候选，不提升为确定地点关系。
邮件正文暂不自动创造人物实体/家庭关系，也不新增地点/日期节点。
现有确认人物关系继续由原有身份图构建流程维护。

这不是通用邮件实体关系抽取器：当前显式日期解析覆盖 ISO 日期和带年份的英文日期，
不猜测缺失年份，不把所有同一天的邮件和媒体强制串联。

## 5. 检索与 Agent

- 邮件参与现有文本语义和关键词召回；视觉通道只处理真实图片/视频。
- 图开启时按结构路径扩展；图关闭时不做邮件图扩展，正文检索仍正常。
- 检索 preview 明确标记 `source_kind=email`、`evidence_id`、`text_content`。
- 新增 `email_text` 证据类型；只在实际返回正文时满足，不能由照片候选冒充。
- 邮件正文直接供模型读取，不调用 OCR 或视觉模型，不执行邮件中的指令或链接。
- 最终证据保留源 `email...` ID；可沿用现有 handle 交付，不强制输出内部资产 ID。
- 正文预览最长 12000 字符。超长邮件仍需后续分块/按需读取，不宣称无限上下文支持。
- 金额/日期答案仍保留现有严格事实绑定：正文里出现数字不自动等于结构化金额或活动时间。
  因此答案正确时也可能显示 `answered_with_gap`，验收报告将其与模型调用失败分开。

PhotoBench 的 GT/交付规范增加 email 类型规范化，`email123.txt` 与源 ID `email123` 匹配。
邮件 QA 不再无条件显示“尚未接入记忆”；有可用后端时读取真实资产进行映射，
并分页读取超过 1000 条的资产列表。没有后端仍可浏览 QA，但明确标记 pending。

## 6. 修正和删除

- 资料库详情沿用记忆修正面板，邮件显示摘要和正文编辑框。
- 正文修正更新 Observation revision，并重新同步关键词、文本向量和图投影。
- 摘要修正同步对应邮件事件标题/摘要；事件摘要/记录时间修正反向同步邮件观察/资产时间。
- 时间保持点事件，拒绝不一致的邮件开始/结束时间。
- 默认正文接口返回最新数据库正文，`original=true` 仍返回原始正文文件。
- 派生同步失败保持待重试，不伪报成功；沿用修正审计和重试接口。
- 删除走普通 Asset 删除入口：清理专属观察、事件、图节点/边、关键词、文本向量和源文件。
- 不删除独立照片，不因为邮件预约被删除而删除真实拍摄记录。
- 原始 ATM-Bench 下载目录不在删除范围；修正/删除操作只影响导入副本。

## 7. 接口和产品入口

接口均在现有 API 前缀和认证/内部调用保护下，无额外绕过：

| 接口 | 用途 |
| --- | --- |
| `POST /api/v1/emails/import` | scope_id、items、rebuild_graph；单次最多500封 |
| `GET /api/v1/emails/{asset_id}` | 正文、来源标识、revision、索引状态 |
| `GET /api/v1/assets/{asset_id}/file` | 最新正文；original=true 读取原件 |
| `PATCH /api/v1/memories/observation/{id}` | 修正摘要/正文 |
| `PATCH /api/v1/memories/event/{id}` | 修正通信事件时间/摘要 |
| `POST /api/v1/memories/corrections/retry` | 重试派生同步 |
| `DELETE /api/v1/assets/{asset_id}` | 普通关联删除 |

产品：“添加资料”→选择具体相册→“导入邮件记忆”→选择 emails.json。
在“资料库”的文本筛选和“时间线”的邮件通信事件中查看，文本不使用图片缩略图。
导入页面使用既有 CSRF 请求封装；部分失败会提示重试，不显示为全部完成。

## 8. 可复现命令

只向明确指定的数据目录和 scope 导入：

```bash
PYTHONPATH=apps/api/src .venv/bin/python scripts/import_atm_emails.py \
  --source /Users/rm003/Downloads/ATM-Bench/data/raw_memory/email/emails.json \
  --data-dir /Users/rm003/sentrix-home-web-2.0/data/email-validation-20261118 \
  --scope-id ATMEmail-20261118-validation
```

真实问答验收脚本只提交原始问题，不将 GT 上传为记忆或放进模型提示词：

```bash
.venv/bin/python scripts/validate_email_qa.py \
  --api http://127.0.0.1:18224 --scope ATMEmail-20261118-validation \
  --qa apps/photobench/datasets/atm-bench-media/qa/atm-email-main-381q.jsonl \
  --output data/email-validation-20261118/qa-acceptance.json --limit 10
```

## 9. 验收记录

- 完整原始邮件：6742/6742 保存、6742/6742 已索引、0 导入错误。
- 验收数据库：`data/email-validation-20261118/sentrix.db`，不是现有正式测评数据库。
- 额外导入三张真实 Fancett 午餐照片，邮件→活动日期→三张照片的三跳路径已验证。
- 路径为 `email_time_candidate`：没有地点确认时未冒充强关联。
- 扩展专项后端 **219 项通过**（83 项图/邮件/数据集专项 + 136 项助手规划/账本/执行回归）；
  包含邮件导入、去重、索引失败重试、正文检索、时间线、
  修正后旧边撤销、时间同步、删除清理、不误删照片、邮件ID分页映射及既有图/删除回归。
- 产品邮件/批量导入前端交互 7 项通过，PhotoBench 邮件 QA 展示 3 项通过；两套前端构建通过。
- 对照原 hpq HEAD，原有前端详情/时间线旧测试为 9 failed / 8 passed，未宣称全套前端绿色。
- 实际 HTTP 导入/修正/删除闭环通过。删除后 Asset、Observation、Event、导入状态、FTS、
  文本向量、图节点/边、正文文件均无该 canary 残留；外键检查通过。
  删除对象只有独立验收 scope 的合成邮件，6742 封真实邮件和三张照片保持不变。
- 最终全库核对：6742 条邮件观察，6742 条完成索引状态，0 索引错误，20226 条邮件文本向量。

### 真实模型问答：固定前10题，不挑选成功题

使用项目已有 qwen35-q4km 推理模型、既有 BGE-M3，不调用独立效用评分模型。
请求均调用正常 Assistant HTTP 接口；QA/GT 只用于验收比较，未写入记忆。
首轮曾与图片导入并发导致轮询30秒超时，照片导入结束后重跑完整固定前10题，
将客户端轮询改为可重试120秒，不篡改服务端成功状态。

| 序号 | 题目摘要 | 人工核对核心答案 |
| --- | --- | --- |
| 1 | BMVC住宿总费用 | 未通过：没有召回两封正确账单/订房证据 |
| 2 | Fitzwilliam Street封路原因 | 通过：路灯维修 |
| 3 | Easter Vacation住宿津贴申请截止 | 通过：5月6日下午5点 |
| 4 | Peterhouse May Ball日期 | 通过：6月24日 |
| 5 | CAISH TA报酬 | 通过：£150 |
| 6 | BRITA水壶价格 | 通过：£19.20 |
| 7 | Formal Hall恢复日期 | 通过：4月27日 |
| 8 | Seagate硬盘单品价格 | 未通过：把含大米的订单总额当成可回答的金额 |
| 9 | Marseille取车时间 | 通过：7月8日15:30 |
| 10 | Christmas Superhall日期 | 未通过：漏召回正确邮件，未完整给出两个日期 |

10/10请求完成、无 worker/model-call 失败，但 **10/10 状态为 partial / answered_with_gap**，
不能称严格证据验收全绿。正文金额/活动时间仍未被结构化事实绑定器充分覆盖，
部分题还存在规划器误选可见文字或全库统计类型的问题。
人工核对核心答案 **7/10**（不是官方Judge分数）；GT证据召回 **8/11=72.7%**，
最终邮件交付命中 **7/11=63.6%**。总耗时384.33秒，中位32.315秒。
这些数字只是固定前10题诊断，不代表完整381题测评结果。

已补充“订单总额不等于单项价格”的正文证据规则，并单独复测第8题；
模型仍只解释了总额包含大米，未明确拒答，因此该问题仍列为未通过，未粉饰结果。
后续要补强文本金额/日期的逐项引用验证，以及邮件多跳召回，不能简单放松绑定门禁。

### 邮件 + 照片真实问答

Fancett原始混合问题经正常Agent检索，候选包含两封GT邮件和三张GT照片，
最终交付三张午餐照片（照片命中3/3；邮件未作为最终附件，GT混合附件为3/5）。
耗时32.39秒，状态partial。图中邮件出发的三跳路径单独保存，可审计。
这里的隔离库只有三张导入照片，不能据此宣称在完整ATM混合数据集上已有图增益。

产物：`email-import-report.json`、`mixed-graph-paths.json`、`qa-acceptance.json`、
`mixed-qa-acceptance.json`、`qa-price-recheck.json`、`lifecycle-acceptance.json`。
这些含真实正文的验收数据不提交到 Git。

### 运行状态与签收范围

隔离验收 API：`http://127.0.0.1:18224/api/v1`。它不是正式产品页面的新端口。
没有将正式页面后端/PhotoBench运行连接改指向验收库，没有覆盖旧数据库或历史测评记录。
要在正式产品相册中使用，重启加载新代码后从“添加资料”显式导入选定相册；
不能把隔离库6742封邮件已经导入，误解成原有相册也已自动拥有这些邮件。

签收：邮件存储/索引/时间线/图关联/修正删除基础能力通过专项验收。
未签收：完整ATM邮件/混合benchmark质量、全部题型严格事实绑定、跨模态总体图增益、全仓库CI。
