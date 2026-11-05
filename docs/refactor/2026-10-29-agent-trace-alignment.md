# PhotoBench 用户轮次、模型调用与工具轨迹对齐

用户要求核查QA详情的多轮对话、Agent Loop、工具与运行时事件，基于58f7f39工作树修订。

## 数据和读侧契约

- QA原question保留。只读摘要增加display_question（已存首轮message）、conversation_turn_count、conversation_context_mode；agent_status补读turn_status/末轮状态。OpenAPI与前端类型同步。
- 工具显式parent_step_id只匹配同一conversation_turn的唯一有效模型尝试；父调用ID可直接匹配。显式关系不成立时保持未绑定，不能跨轮或按工具数组位置猜。旧记录无父标识时保留已有合法绑定。
- 完整模型IO按call_id或同轮call_type/step_id/attempt关联。失败尝试不借用成功重试的提示词。只有模型指标的历史结果仍展示调用事实，提示完整IO未记录。
- 时间线在每轮内部合并模型、工具、证据核对及收尾事件；未知父调用的工具保留。原始事件单独折叠核验。事件时间只有秒级时不扩展时间窗口推断先后。
- 用户对话轮、模型调用序号、工具执行分别计数。final_answer是交付工具，结果回答块不是新增模型请求。

## 采集修正

ClaimVerifier此前在include_debug时保存IO，却未写session.model_calls；现在每次真实模型请求都生成模型指标，成功/失败均记录duration、status及错误，debug关闭也保留。RunTurn追加该指标，不改核验决策、预算或恢复策略。Agent debug新增相同call_id便于核对，未修改历史存档。

## 页面与计时

- 列表命中数及分母优先使用已存media_retrieval_counts，与媒体评分口径一致；旧记录保留原字段读取，不改写存档。
- 列表显示首轮问题与多轮徽标。详情先显示全部用户轮次、各轮回答、实际行为及评分；多轮执行步骤按轮展开。
- 每轮证据账本位于该轮执行步骤内；不再把首轮账本冒充整题。性能树作为补充折叠入口。
- metrics_contract_version=2的wall_clock_ms是Agent请求墙钟，外部Judge另列。工具读duration_ms/latency_s，null保留未知。
- 工具内模型耗时已包含于工具累计，主模型累计排除tool_internal，避免重复累计。累计时延大于墙钟时不绘制组成条，保留各自用时。
- policy_refusal且无调用记录时，明确显示规则直接拒答；不再提示历史记录缺失。其他缺失字段不补造。

## 验证与边界

实机存档回放：100婚礼题2轮、8模型、6工具；规则拒答0/0；153动物园三题4/2、6/4、3/2。合计21模型、14工具逐条与时间线对应；13条有完整IO。100婚礼题原存档没有完整模型IO，不能恢复。

五份API原始样本、离线核对结果、浏览器截图和测试日志保存在产品工作区audit/20261029-agent-trace-alignment。验证使用已保存真实结果与脚本模型，不发真实模型/评分请求。部署与新运行采集验收另行登记。本轮不改历史评分、QA、GT或四机数据。

已知完整性边界：HTTP客户端内部重发仍沿原有采集口径，不能将逻辑模型调用数解释成全部传输请求数；原始秒级事件无法恢复更细时序。
