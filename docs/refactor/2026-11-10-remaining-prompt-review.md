# Sentrix 剩余模型可见文本集中审查稿（已确认）

基线：GitHub `main`，HEAD `65ce640bc7697970c3a414eb2205d22accd658cf`。本稿记录集中审查时的原文与提案；第 1～3 节的八项已由用户于 2026-11-10 整批确认，并并入 `2026-11-09-prompt-cache-optimization-plan.md`。业务代码尚未修改。示例 JSON 的值用于展示完整字段形状，实施时按真实值填充。

## 覆盖范围与状态

| 模型输入阶段 | 审查状态 | 本稿覆盖项 |
| --- | --- | --- |
| 离线图片观察、失败恢复、事件总结、人物时刻/画像、家庭关系、因果判断 | 固定提示词与输入包已逐项讨论并记入主计划 | 家庭关系 B 版片段预算与写入条件仍待后端设计 |
| 在线规划器、主 Agent system、工具定义、L2/L3、看图 VLM | 固定提示词与结构已逐项讨论并记入主计划 | 人物照片事实的在线按需读取尚未定稿 |
| 在线异常重试、工具成功结果、最终回答软门禁 | 本稿集中列出已确认改法 | 已确认四条重试文案、三份工具结果、一条软门禁 |
| JIT 的每轮工具列表与历史压缩 | 已识别实现机制和备选方案 | 实际 GGUF 模板与缓存表现留待部署后实验 |

这里的“剩余”指集中审查时正常业务路径中仍可能送给模型、当时尚未形成确认版的文本或字段；第 1～3 节现已确认。工具报错码及其失败说明、L2/L3 的具体裁决理由继续按原有错误语义发送；它们不是固定前缀，也没有统一压短的可靠收益。

## 1. 在线 Agent 四条纠偏消息

来源：`apps/api/src/sentrix/application/assistant/run_turn.py` 的四个常量。它们均作为追加的 `user` 消息发送；主 system、原生历史和已有工具往返不因这些文案改变。

### 1.1 空回复 `PARSE_FAILURE_NUDGE`

当前完整文本：

```text
你上一条回复是空的。请直接用一两句中文把结论说给用户，不要再调用工具。
```

修改后完整文本：

```text
上一条回复为空。若仍需证据且有可用工具，先取证；否则依据已知内容作答，不确定就说明。
```

原因：当前空回复不代表已经取证完毕；固定禁止工具可能逼模型无依据收尾。提醒仍只发一次，后端预算与门禁不放宽。

### 1.2 正文伪工具调用 `TEXTUAL_TOOL_CALL_NUDGE`

当前完整文本：

```text
你上一条回复里把工具调用写成了 `<tool_call>` 标记的形式，那不是一次真正的工具调用，用户会原样看到这些标记。要调用工具请使用原生工具调用；要回答用户就直接用中文说。请重新给出这一轮的回答。
```

修改后完整文本：

```text
上一条把工具调用写进了正文，该调用没有执行。需要工具请用原生工具调用；否则直接回答，不输出调用标记。
```

原因：保留“正文标记没有执行”的关键事实，删掉重复解释和字面 XML 标记。现有识别正则与只纠正一次的循环规则保持。

### 1.3 连续无效工具调用 `STALLED_NUDGE`

当前完整文本：

```text
你连续几轮调用的工具都没有返回可用的结果。请立即基于已经取到的内容作答；确实拿不到的就如实说明拿不到。不要再调用刚刚被拒绝过的工具。
```

修改后完整文本：

```text
连续调用未取得新证据。不要重试刚被拒的工具；根据已有内容回答，缺口如实说明。
```

原因：保留防止同一工具重复失败与如实回答的动作；触发阈值和后端失败工具过滤不变。

### 1.4 未尝试必要需求 `UNATTEMPTED_NUDGE_TEMPLATE`

当前完整模板和动态字段：

```text
还有声明的证据需求尚未尝试：{pending}。当前可用工具：{available}。请先用兼容工具获取证据；如果确实没有对应的照片或记录可以确认（例如问题本身没有可查的答案），请直接输出 final 如实说明，不要编造。
```

当前 `pending` 为 `req_1:photo_set` 一类内部 ID 加类型；`available` 为重复列出的工具名。修改后完整模板：

```text
尚有必要内容未核对：{pending}。请用当前可用工具获取；确实取不到就说明缺口，不要猜测。
```

新 `pending` 从规划后的 `predicate` 和 `evidence_type` 生成，例如 `相关婚礼照片（photo_set）`；不发送 `req_N` 内部 ID，也不重复工具列表。触发时须确认当前 JIT **确有能满足其中一条未尝试需求的工具**，不能只看工具数组非空：只有无关的 `final_answer` 时提醒“去取证”没有执行出口。若同类型有不同谓词，应分别列出，不把不同需求合并。

## 2. 三份在线工具结果的模型可见字段

这些是成功工具调用后的 `tool` 消息，不是工具 schema。简化会缩短**后续** Agent 模型调用的输入；不会改变已发生的工具调用那一步的前缀。内部 `observation` 保留实际资产 ID、来源和审计信息。

### 2.1 `read_photo_text`

来源：`apps/api/src/sentrix/application/assistant/tools.py` 的 `_read_photo_text()`。当前完整成功结果示例：

```json
{
  "asset_handle": "photo_1",
  "full_text": "合计 3000 元",
  "text_regions": [{"text": "合计 3000 元", "source": "ingestion_ocr"}],
  "certainty": "supported",
  "source": "ingestion_ocr",
  "note": "这些字是导入时从画面里读出来并保存的。"
}
```

修改后完整成功结果：

```json
{"asset_handle":"photo_1","full_text":"合计 3000 元"}
```

`text_regions` 是同一 OCR 文本的前八行截取，没有坐标信息，向 Agent 主要重复发送文字；界面没有文字框需求。实施前须保证裁剪后的 `full_text` 仍覆盖原先 `text_regions` 能提供的内容；若有超出，应调整单份正文的裁剪规则，不能静默丢失可见文字。`certainty` 恒为 `supported`，`source` 恒为 `ingestion_ocr`，工具简述已说明读已存文字。`note` 仅供当前进度文字读取；实施时把进度文案改由内部观察或常量产生，不能因移除模型可见 `note` 让进度空白。L3 已确认的文字证据继续从 `full_text` 读取，失败结果继续明确区分“未存文字”和“画面没有字”。

### 2.2 `query_photo_people`

来源：`apps/api/src/sentrix/application/assistant/tools.py` 的 `_photo_people()`。当前完整成功结果示例：

```json
{
  "result_set_id": "rs_0123456789ab",
  "people": [{"person_name":"妈妈","membership":"母亲","identity_status":"bound"}],
  "unconfirmed_people_count": 1,
  "summary": "妈妈；另有 1 名未确认身份同行者"
}
```

`result_set_id` 的删除已在执行文档中确认，本次进一步确认的完整结果：

```json
{
  "asset_handle":"photo_1",
  "people":[{"person_name":"妈妈","family_role_label":"母亲"}],
  "unconfirmed_people_count":1
}
```

`summary` 与名字和人数重复，`identity_status` 对返回的 `people` 恒为 `bound`，均可不让模型重复阅读。当前 `membership` 实际装的是自由文本家庭角色标签（例如“母亲”），改名 `family_role_label`，避免误解成 `family/friend` 归属或两人关系边；无角色标签时省略此键。新增 `asset_handle` 让多张照片的核对结果可区分；后端仍按内部资产 ID 绑定，证据投影只读 `person_name`。`get_person_profile` 仍是归属与具体关系的权威工具。

### 2.3 `inspect_photo`

来源：`apps/api/src/sentrix/application/assistant/tools.py` 的 `_inspect_photo()`。当前完整成功结果示例：

```json
{
  "asset_handle":"photo_1",
  "question":"妈妈穿什么颜色的外套？",
  "observation":"目标人物穿红色外套。",
  "certainty":"supported",
  "target_person":"妈妈",
  "target_face_status":"located",
  "photo_identities":[{"person_name":"妈妈","membership":"母亲","identity_status":"bound"}],
  "face_candidates":[{"face_id":"face_1","identity_status":"bound","person_name":"妈妈"}],
  "unconfirmed_people_count":0
}
```

修改后完整成功结果（有指名目标人物时）：

```json
{"asset_handle":"photo_1","target_person":"妈妈","observation":"目标人物穿红色外套。","certainty":"supported","target_face_status":"located"}
```

未指名目标时省略 `target_person/target_face_status`。`question` 已在调用参数中，重复返回无助于判断；`target_person` **保留后端最终选中的姓名**，因为它可能从问题及照片身份中推断，未必等于调用参数。照片人物身份列表由 `query_photo_people` 专门核对，故不在看图结果重复返回身份列表与人脸清单。实施时将 `INSPECT_PHOTO.satisfies` 收窄为 `VISUAL_OBSERVATION`，移除证据投影从 `inspect_photo.photo_identities` 产 `photo_identity` 的分支；`photo_identity` 由 `query_photo_people` 满足。后端观察仍保存目标定位与模型调用审计。成功 `observation/certainty` 保留，`certainty` 缺失/非法按先前已确认方案处理为 `uncertain`。

## 3. 另一条在线软门禁提醒

来源：`apps/api/src/sentrix/domain/assistant/final_guard.py` 的 `_MESSAGES["denial_without_search"]`。当前完整提示词：

```text
你的回答声称没有找到相关记录，但本轮没有调用任何检索工具。请先调用 search_memories 完成检索，再基于工具结果回答。
```

修改后完整提示词：

```text
你说没有找到相关照片，但本轮尚未检索照片。请先检索；无法检索时改为“现有记录无法确认”。
```

触发同步限定为当前问题确需照片存在性判断、且本轮没有成功照片检索或其他**同范围**的存在性证据。`query_album_stats` 若已给出匹配问题范围的零计数结构化结论，不能因没调用 `search_memories` 就被误拦；普通对话也不触发。该项需要让 `FinalGuard` 读到规划路由/需求以及成功统计事实及其筛选范围，不能只改文案。其余软门禁消息、L2/L3 判后纠错消息均保留现有针对性细节，避免为了省少量字丢失重答依据。

## 4. 已核对但无需修改的现有提示词

- `get_result_page`：当前固定检索最多 6 张、页宽也是 6，正常路径不会向模型提供翻页 schema；既有执行文档已要求未来放宽候选数时先由后端注入结果集 ID。当前缩短它没有运行时收益。
- `query_album_stats` 的成功返回：当前 `FactAnswer.visible()` 仍包含 `status/fact_type/subject/value/total/unit/rows/filters_applied/samples/coverage`。用户先前明确要求“统计返回字段本轮不精简”，本稿沿用该决定；此项若要再动须另行确认，不能把输入工具的五字段精简误写成返回也已精简。
- `final_answer` 参数 JSON 解析失败的短提醒：当前“final_answer 的参数不是合法 JSON，请重新调用一次。”已有明确动作，无重复证据内容。
- L2 `JudgeVerdict.reminder`、L3 未支持 claim 的重答提醒与其他软门禁文案：保留具体裁决原因，模型需要它修正答案；这些只在失败路径追加。
- 工具策略的预算/重复调用拒绝消息：失败路径才出现；`search_memories` 重复调用中仍提 `get_result_page/result_set_id` 的错误文案已经在主执行文档确认替换，其余暂无比正确拒绝理由更有价值的文本压缩。

## 5. 尚未定稿的设计项，不冒充已确认提示词

1. **在线人物照片事实查询**：离线 `person_visual_fact` 已规划按 `face_instance_id` 入库，但普通 `get_person_profile` 当前不返回动作/衣着/所持物等当次事实。候选设计是复用已确认的 `get_person_profile(person)` 工具定义，后端按规划需求与本轮问题仅在需要时附相关、当前归属于此人的 `photo_facts`；这样不增工具 schema 和模型填写字段。需要单独审定可检索范围、照片来源、事实数量与规划器/JIT/证据绑定映射；不能把全部人物事实塞进普通档案返回。
2. **家庭关系 B 版证据片段与写库门禁**：提示词和事件中心 JSON 已确认；事件片段上限、独立事件认定和朋友/关系边写入仍待定。这些是输入选择与后端判定，不是新的固定提示词。
3. **JIT 工具包与 KV 缓存**：逐步 JIT、整轮固定精简工具包或全量工具的选择，用户已要求部署后按实际 GGUF 模板、缓存 token、总预填量和误调工具情况实验决定；本稿不提前选定。上下文压缩重写旧轮消息、`final_answer` 单工具时的客户端 schema 注入也须在该实验中看实际序列化前缀。

第 1～3 节已并入主执行文档；第 5 节设计项仍按原有待定状态处理。
