# .119 xy目录整理

目标为asus@192.168.1.119的/home/asus/xy。脚本先生成计划，记录进程/服务/定时任务/运行与发布目录链接中的路径引用；活动目录保留。散文件按脚本、日志、包、证据归档，历史验证目录归入archive/experiments。PhotoBench原始数据与数据包归入datasets/photobench，与既有datasets/ATM-Bench并列。三条原数据路径保留链接以服务冻结历史中的绝对路径；新数据操作使用规范路径。

代码先在Mac验证提交，再同步本脚本到远端audit/20261124-server119-layout。按顺序执行`python3 organize.py plan`、检查plan.json后执行`python3 organize.py apply`；apply启动标记防止盲目重放。verify可重复运行。脚本不重启服务，不删除旧文件，不修改业务源码/数据库；同盘rename后按文件SHA/目录inode、服务PID及HTTP首页内容核验。根README、datasets/README及archive/README生成目录说明和逐项路径映射。旧脚本和原始证据内的路径保留其当时事实。

全部计划和实际移动记录在远端audit/20261124-server119-layout；产品项目同名审计保存副本。若apply中断，先读取moves.jsonl确定已完成部分，不重复执行；按记录逐项逆序rename可恢复旧布局，数据路径的链接需在恢复对应目录前解除。正常完成后，verify应无差异。
