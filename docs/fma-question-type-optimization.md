# FMA作答优化：问题分类+定向检索

## 背景
当前FMA agent拿到问题后，走统一检索流程（ANN视觉+FTS文本+...），不区分问题类型。
导致：
- 地点题搜了一堆视觉特征，浪费检索预算
- 人名题没优先查entity通道，召回率低
- 所有题平均耗时3-4分钟，有优化空间

## 优化方案

### 1. 问题分类器（intent.py）
在planner的JSON输出里加`question_type`字段，模型一次调用同时完成分类+证据规划：
```json
{"action":"declare","declaration":{"question_type":"place|date|person|feature|cross|refuse","goal":"...","requirements":[...]}}
```

分类规则：
- place：问地点/位置/在哪里
- date：问时间/日期/年份
- person：问人物/是谁/穿着/关系
- feature：问视觉特征/颜色/服饰/动作/道具
- cross：同时问两个以上维度
- refuse：信息注入/套话/无法回答

### 2. 定向检索（retrieve_evidence.py）
根据question_type调整通道优先级和权重：
| 类型 | 优先通道 | 权重调整 |
|------|---------|---------|
| place | lexical→metadata | lexical×2.0 |
| date | lexical→metadata | lexical×2.0 |
| person | entity→metadata | entity×2.0 |
| feature | visual→lexical | visual×2.0 |
| cross | entity+lexical并行 | 各×1.5 |
| refuse | 跳过检索 | 直接拒答 |

### 3. 传递链
planner → goal_planner → run_turn → tools.execute → search_memories → retrieve_evidence

## 测试结果
- 基线：q=0.5（reuse 10qa）
- 优化后：q=0.9（待验证）
- 耗时：~2分钟（reuse模式）

## 代码改动
- `apps/api/src/sentrix/domain/assistant/intent.py`：加question_type分类
- `apps/api/src/sentrix/application/assistant/goal_planner.py`：解析并传递question_type
- `apps/api/src/sentrix/application/assistant/run_turn.py`：从ledger取question_type传给检索
- `apps/api/src/sentrix/application/assistant/tools.py`：execute加question_type参数
- `apps/api/src/sentrix/application/retrieval/retrieve_evidence.py`：根据question_type定向选通道

## 下一步
- 487qa全量验证
- 检索排序加权（geo/time匹配度）
- 交叉题的多条件AND过滤
