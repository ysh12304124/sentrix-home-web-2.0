# PhotoBench 题型评分

本页保留evaluation-2026.11.03-v1的历史协议，供旧快照追溯。
当前新测评与补评采用[双评分标准](photobench-dual-scoring.md)：
按所选标准执行评分，新QS列表统一Jaccard，数值题由Judge对照参考答案匹配。
下文的open_scale、any/all和混合汇总仅描述旧协议。

实现版本：evaluation-2026.11.03-v1。原始QA通过独立evaluation字段追加定义，历史无定义的run沿原0/1/2量表读取。

## 定义与运行配置

定义包含answer_format、scorer、max_score、applies_to_turn（从0开始），以及当前QA内容指纹。choice定义还含options/correct_option，媒体列表含target_media_refs和retrieval_requirement（all/any）。ATM保留source_qtype与source_qa_id。

创建run的evaluation_options示例：

```json
{"open_scale":"ternary","choice_semantic_aux":false,"number_semantic_aux":true}
```

open_scale可选binary/ternary。配置及完整提示词随run冻结，续跑继承；重判保存自己的配置、提示词与旧评分历史。选择题选项和答案键只在评测端使用，Agent请求仍是开放问题。

## 主分与辅分

choice_judge先让Judge逐项语义比较，程序核对正确项。额外other须完整排除全部选项，0分；选项重叠needs_review；调用和格式错误保留未评分。原选项中的“其他/以上皆非”仍是普通选项。

media_set/atm_list以实际交付附件为预测，按media_type+media_id去重，视频关键帧转回父视频。all使用Jaccard，any按任意目标命中。交付对答案target，工具累计对evidence，两套指标均保存Recall/Jaccard/Precision/F1及集合明细。累计仅到当前轮，缺轨迹不能冒作空集合。

atm_number主分采用本目录固定官方实现；语义辅分先在不看GT时归一化，再与GT匹配。atm_open采用官方二值提示词。PhotoBench开放题默认0/1/2，可切binary使用独立二值提示词。辅分失败保留有效主分，取消信号继续透传。

## 展示与导出

每个结果保存score/max_score/normalized_score/judge_status/version，Judge调用保存输入、输出和模型。分类筛选的旧0/1/2映射只表示错误/中间/满分，连续原值保留；二值不显示部分正确类别。题型汇总显示有效覆盖和逐题归一化均分，覆盖不全时不称完整QS。

HTML混合新旧量表时，旧分除2后与新版归一化分求均值。题型汇总单列新版定义分母。CSV、HTML和轨迹导出保留定义与结果。行为正确性独立统计。

成功检索/翻页轨迹追加returned_asset_ids/page和累计并集，原retrieved_candidates字段含义保持。ordered_calls仅保留有序证据，当前不计算官方Recall@k/Recall@kGT。

## 数据构建与验证

scripts/build_benchmark_adapters.py从固定ATM提交和当前PhotoBench1171/487生成新目录，必须提供逐题复核文件。原始QA字段保持；同题定义一致。reference模式使用原媒体软链接，跨机发布前使用copy重建或绑定同SHA源。

本轮候选与可复现命令见产品项目audit/20261103-typed-evaluation/README.md。自动文本复核的verified不代表人工审核或全量视觉事实验收。正式发布前须完成标准12QA和新题型Agent小样；真实Judge样例与Agent端到端验收分开。

## UI与ATM小测

评分定义、选项、结果和汇总使用独立卡片样式，正确项有文字标记。启动区按评分设置、运行操作分行；搜索框与下拉框统一边框、圆角、内边距和焦点态，窄屏自动换列。

`scripts/build_atm_smoke.py --source <typed-root>/atm-bench-media --output <data-root>/atm-bench-smoke10`
生成固定10题的小测相册，13张图片和3个视频，共16媒体，来自main6/hard4；数值3、列表3、开放4。保留原题行、评分定义与媒体字节，包含target/evidence并集，并物化复制媒体。默认入口`atm-smoke-10q`，另有main6/hard4入口。能力矩阵、来源和SHA在生成包README与audit中。小测用于流程验收，不能替代官方全库QS或PhotoBench选择题验收。
