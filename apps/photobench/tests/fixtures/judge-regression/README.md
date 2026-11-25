# Judge隔离小样

`cases-v1.json`保存完整问题、参考、答卷、原生题型和预期审核状态；`prompts-v1.json`冻结本次当前提示词与官方开放题原提示词。真实Wi-Fi疑点没有预设正确分数。工程构造样例的预期值尚未经独立人工审核。

在仓库根目录运行，使用安装PhotoBench依赖的Python：

```sh
PYTHONPATH=apps/photobench/src python apps/photobench/tests/fixtures/judge-regression/run_samples.py \
  --connection /absolute/private/connection.json \
  --output /absolute/audit/new-sample-directory \
  --actual-model doubao-seed-2-1-lite-260915
```

连接文件包含`url`、`model`、`api_key`，由调用者单独保管，不加入Git。可以重复传入`--case 样例ID`限定范围；默认每项三次。每次先写意图，再保存无授权头的完整请求、响应、实际模型和耗时；已有结果复用，只有意图而没有回执时停止，不能自动重发。网络、格式或实际模型版本异常保留未评分。该工具不启动Agent，不写正式批次。

当前ATM开放题与数值题按生产请求字段构建；开放式0/1/2调用生产`JudgeRunner`捕获task_v4请求，包含交付与工具编号说明。GT关闭，空别名列表。官方对照仅用于ATM开放题；数值题另记录仓库固定版本`ef4e5dff`的规则计算。列表题走既有Jaccard单测，不调用Judge。

2026-11-11验收共66次真实Judge请求：初轮57次，加生产task_v4开放式9次。初轮20条布尔/分数字符串被测试脚本误拒，按应用解析规则离线重解析后57条均有效，无额外请求；原回执保留。13个样例各三次同分，工程预期全部吻合；Wi-Fi当前与官方均1/1/1，历史mix判0保留。没有据此改提示词或全631复评。完整请求、理由和差异报告保存在本次独立审计目录，仓库仅保存可复用输入与运行器。
