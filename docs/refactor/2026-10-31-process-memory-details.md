# 进程内存明细修复

Jetson（NX、AGX）读取每个PID的PSS后，同时更新用于导出的去重列表。
`benchmark_processes`保存同一次读取的PSS、smaps RSS和进程启动标识。
合计只在相关进程读数完整时发布，单进程缺失不抹去其他进程的原始明细。

Mac的`model_system_processes`和`benchmark_processes`保存每个PID的内核
`ri_phys_footprint`原始字节、MiB、进程启动标识、来源、时间和状态/失败原因。
读取失败或PID身份变化的值保持未知，成功进程的明细保留。
主模型总量继续使用phys_footprint；产品组总量继续使用原有footprint组驻留统计。
产品组驻留与逐PID的phys_footprint是不同口径，不要求两者相加相等。
GPU分配口径和历史采样均保持。

本修复不改变采样频率、模型参数或评分。按用户要求，偶发采样缺失只记录，
暂不作为本次修复的强制通过条件；这是本次验收政策，不改写历史验收结果。

验证包含进程去重、成功与缺失混合、PID复用、同口径求和，以及JSON保存/读取。
设备回归使用部署后的真实provider与GpuSampler，保存独立审计样本，
不创建新问答或模型推理请求。
