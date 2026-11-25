# 统一文本记忆：实现与验收（2026-11-18）

## 当前实现

邮件、笔记、文档正文都进入 `ImportTextMemories`，保存为 `media_type=text` 的资产，
观察统一为 `source_type=text`。来源类别只保存在 `source_metadata.source_type`，
例如 email、note、document、unknown；它不选择另一套构建流程。

入口：`POST /api/v1/text/import`，同步返回200和 completed/partial，**没有假装已接入202异步队列**。
一批最多500条；前端分批50条，最后一批刷新图。原文、词法索引、BGE文本向量共用一套流程。
有已有摘要则复用，否则首行提取短标题；本轮没有增加大模型自动摘要/通用实体抽取调用。

```json
{
  "scope_id": "album-001",
  "items": [{
    "external_id": "record-001",
    "text": "蓝桥计划会议地点是海棠厅。",
    "title": "会议笔记",
    "recorded_at": "2026-11-18T10:00:00+08:00",
    "metadata": {"source_type": "note"}
  }],
  "rebuild_graph": true
}
```

- 只有 scope_id 和非空 items/text 必须提供。
- external_id 缺省时按输入内容生成稳定ID，重复提交不会重复创建。
- recorded_at 缺省保持未知；不能把上传时间当作记录时间或活动时间。
- 相同ID、不同内容或不同来源标签返回冲突，不覆盖人工修正。
- 来源标签由输入方提供，不声称能从任意裸文本准确识别邮件。

## 接线范围

1. `/text/import` 和 `/text/{asset_id}` 为统一入口/详情。
2. `/emails/import` 仅是 ATM 字段适配器：id→external_id、detail→text、
   short_summary→summary、timestamp→recorded_at，补上来源email后调用同一文本入口。
3. `/emails/{asset_id}` 兼容读取同一个文本记录，不另存一份邮件记忆。
4. 普通 `/media/import` UTF-8文本上传进入统一文本用例。
5. 相册/持久任务的 `ImportAssets.stage` 通过文本协作者调用同一个用例，跳过图片理解和人脸处理。
   已完成文本任务正确记录实际阶段并触发批末图更新，不冒充去重或视觉处理成功。
6. 前端“添加资料”改为“构建文本记忆”：直接输入、TXT、Markdown、文本数组JSON、ATM邮件JSON。
7. 文字修正、索引重建、默认正文读取、删除均扩展为所有文本，不再依赖email类型判断。

## 兼容迁移

`0037_unified_text_memory.sql`（并入 main 的流水线、来源地址、人物同步和外观任务迁移后顺延）：

- 将旧邮件观察转换为text，保留email来源标签。
- 将旧通信事件转换为text_record，保留原来源信息；它是文本记录，不是正文活动本身。
- 将导入状态表统一为text_import_state；email_import_state为只读兼容视图。
- 保留原资产/观察/事件ID、外部证据ID、正文、时间、revision、向量与修正历史。
- 跨事件关联统一命名为text_refers_to_event/text_time_candidate。

图仍只有既有资产、事件、确认人物等节点。时间、地点和观察载荷保持属性。
文本预约引用日期可关联媒体事件；只有日期吻合时为弱候选，不证明到店、付款或因果。
现有显式日期规则仍有限，不声称已支持任意文本的完整事件关系理解。

迁移验收使用上一轮隔离库的SQLite备份，正式测评库和原始下载目录没有改动。
6742封旧邮件逐条比较：ID、资产ID、外部ID、正文、摘要、revision和记录时间完全保留。
重传原始邮件仍复用旧记录，不因新资产ID前缀不同而创建重复数据。

## 检索、回答和展示

- 所有文本preview统一为source_kind=text、media_kind=text，并提供text_content、evidence_id和来源标签。
- 通用证据类型改为text_content；旧email_text只作为历史兼容。
- 正文直接提供给Agent，不调用OCR，不将正文指令当系统指令。
- 搜索工具/`GET /search` 支持可选source_type筛选，例如email或note；
  未限定来源时不筛选，混合图文问题不强制限定为邮件。
- 来源筛选在交付截断前对候选集执行，但仍受已有召回池大小限制，不声称遍历全库。
- 文本附件按text交付；API显式返回media_type，附件持久化和历史回放保留类型。
  旧TXT/Markdown附件缺少类型时兼容识别为文本。
- 提问页面的附件和候选墙显示正文链接，不请求文本的图片缩略图。
- 压缩上下文时保留正文和来源标签。

真实验收发现英文词法噪声：原分词会把Latin单词拆为两字片段，使TA匹配到单词内部。
现在仅对文本记忆进行原文英文整词校验并按文档频率加权，图片/视频原有计分不变。
没有为某道题硬编码关键词、答案或GT。修复前CAISH题曾失败，修复后正确证据升到词法首位，
用同一原始问题重测正确回答£150。

## 验收环境与结果

- 数据目录：`data/text-validation-20261118`，正式测评相册未切换。
- 隔离API：`http://127.0.0.1:18225/api/v1`，不是产品页面的新端口。
- 模型：沿用正在运行的qwen35-q4km和BGE服务，无新增评分模型、无权重替换。

### 真实问题

| 问题 | 预期核心内容 | 结果 |
| --- | --- | --- |
| 笔记里蓝桥计划的会议地点？ | 海棠厅 | 正确，工具明确筛选note |
| 邮件里蓝桥计划的会议地点？ | 松涛厅 | 正确，工具明确筛选email |
| 纸鹤计划需要准备什么？ | 绿色记录本、一把尺子 | 正确，未限定来源 |
| TXT中山岚计划的集合地点？ | 清风亭 | 正确 |
| Fitzwilliam Street封路原因？ | 路灯维修 | 迁移旧邮件后正确 |
| 2022 Easter Vacation津贴截止？ | 5月6日下午5点 | 迁移旧邮件后正确 |
| CAISH TA报酬？ | £150 | 英文召回修复后正确 |
| Fancett午餐照片？ | 三张目标照片+预约上下文 | 三张照片交付正确，邮件上下文可读 |

其中前4题是隔离人工构造的输入和独立问题，用于验证来源分离、普通文本和文件上传；
后4题使用原始ATM问题和原始记忆。没有将原始QA答案导入记忆。
上述是代表问题核心答案人工核对，不是完整ATM测评或官方Judge分数。
有些正确答案仍为partial/answered_with_gap：结构化地点、金额、活动时间的严格绑定尚未补齐，
没有放松门禁把这些状态强行改为complete。

### 功能回归

- 主专项270 passed / 1 deselected；额外文本preview/交付3 passed，附件持久化/收尾2 passed，
  合计275项通过。
- 前端文本导入、批量导入、候选墙11 passed；文本附件展示1 passed；生产构建通过。
- 真实HTTP导入→去重→修正→索引同步→删除闭环通过。
- canary删除后检查资产、观察、事件、导入状态、词法/FTS、向量、图节点/边和原文文件，无残留。
- 6742条旧记录逐项保留、旧邮件接口幂等兼容通过。

扩展检查发现的既有失败保留报告：

- 助手工具旧测试11项失败；原hpq HEAD同样11项失败，新文本断言另行通过。
- m4a MIME测试期待audio/mp4，但本机返回audio/mp4a-latm；原HEAD同样失败。
  主专项明确排除此一项，没有称全仓库CI绿色，也没有改音频逻辑来掩盖环境差异。
- 扩展的会话仓储测试另有2项旧调用缺少fence/attempt_no/now；HEAD的append_event签名
  同样要求这三个参数，本次未为迁就旧测试而放松并发围栏。

产物位于验收目录：text-qa-final.json、migrated-email-qa-final.json、caish-recheck.json、
mixed-qa-recheck.json、lifecycle-and-migration.json，不提交真实正文到Git。

复现生命周期检查：

```bash
.venv/bin/python scripts/validate_text_memory.py \
  --api http://127.0.0.1:18225 \
  --database data/text-validation-20261118/sentrix.db \
  --previous-database data/email-validation-20261118/sentrix.db \
  --email-source /Users/rm003/Downloads/ATM-Bench/data/raw_memory/email/emails.json \
  --output data/text-validation-20261118/lifecycle-and-migration.json
```

仍未实现：邮箱自动监听、完整长文分块/按需读取、通用文本实体自动抽取、图局部增量更新。
正文preview仍有长度上限，超长文档不等于已经支持无限上下文。
正式运行服务未改指到验收库；使用新接口需让正式后端加载本轮代码和迁移。
