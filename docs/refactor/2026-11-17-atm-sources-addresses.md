# ATM 邮件、Hard 子集与来源地址

新增标准文档记录及 ATM 邮件适配器：`POST /api/v1/documents/import` 接收官方邮件 JSON、标准 JSON/JSONL、TXT/Markdown。批次先校验，逐封保存正文、标题、官方摘要、原对象、来源ID和原时间。无时区时间保持无时区。原包按 SHA 保存；相同来源重导复用，不同正文或元数据须显式新建版本ID。

短邮件独立索引标题/摘要/正文；长文按实际 tokenizer 的 token 预算分块，向量元数据保存文档ID、原文字符偏移和分块协议。删除清理所有分块和外部索引账本。`SENTRIX_DOCUMENT_TOKENIZER_PATH` 可指定本机 tokenizer；WeMM 同机部署可从侧车 health 解析模型目录。默认预算2048，legacy上限512，预留64个包装token。

检索支持 all/email/document。模型先 `read_source_text` 读取，再用 `final_answer.source_citations` 引用结果集与句柄。服务验证本轮读取、范围和scope；保存来源ID、时间、片段及偏移。产品聊天、测评聊天、题库GT和逐题结果展示原文。

测评分开保存图视频 typed refs 与邮件/文档 source refs；分别统计候选、阅读和最终引用。邮件列表按实际来源交付集合评分；Fancett 的三张照片是列表答案，两封邮件是支撑证据。来源定义、交付和处理清单进入指纹、快照与导出，旧题及旧答卷指纹保持。

精确地址以自定义 XMP 写入新图片/视频副本，并保存同值清单。产品导入直接读取地址，优先于GPS反查；资产、地点、检索、详情和视频关键帧继承同一来源。运行期读取XMP无需ExifTool。已有原媒体不原地修改。

## 题包

本机：`/Users/jzousz/Documents/PhotoBench-data/candidates/2026.11.17-atm-sources-addresses`。

- 完整资料库：3759张图片、533段视频、6742封邮件。
- `atm-bench-hard`：官方31题；`atm-bench-hard-email`：其中8道涉及邮件的题。
- `atm-bench-email`：主集381道邮件题。
- `atm-bench`：保留官方1013题；有一处官方列表答案ID与证据ID不一致，预检阻止直接启动。
- `atm-bench-validated`：1012题，明确记录排除的 `atm-500f1059-8d20-4bd7-be43-32ef39bee511`，未改官方原题。

`scripts/prepare_atm_sources.py` 构建独立包；`scripts/verify_atm_sources.py` 核对地址、原件SHA、JPEG原EXIF/像素、视频原标签/压缩流。全4292媒体保持核验已通过。QA和notes留在测评侧，不进入模型记忆。

## 发布

`deploy/photobench/20261117-atm-sources-100/` 从已提交HEAD与0608d54重建四类发布变体，保留既有8191/8291/测评运行补丁。提交后构建并传输，发布前实时检查六服务空闲、源码与历史快照；备份SQLite后迁移，再重启配对服务。先smoke验收再push。单次安装/测试脚本不可重放。

本地543项后端专项通过；原助手工具11项失败与HEAD基线一致，聊天客户端三个嵌入服务依赖失败与HEAD一致。修正输出预算测试，显式包含既有128token安全预留。测评前端806项及新增3项来源组件测试通过；产品前端155通过、31项既有失败保持。两端生产构建、ruff、OpenAPI、模块地图及11条分层契约通过。mypy74处/30文件既有错误，错误集合与原基线相同、新增0，未声称全量CI通过。实际发布与smoke结果见总工作区本轮审计。

## 2026-11-18 发布验收

ed74c7b提交后发布100的8191/8291/8294与8772/8872/8874，GT前端20cfede和导出/独立产品入口40365bb局部覆盖均有SHA回执。三个catalog已注册完整资料库及Hard31/涉邮件8题。完整数据11084文件SHA通过，4292媒体与官方原件保持。

三产品6742邮件预检、逐封导入/幂等/text_ann/地址测试通过；8874邮件2题及Hard3题completed、两种导出来源字段完整。QS分别50%与54.1667%，功能测试不作官方成绩。原903/148/83历史文件、保护配置与十条旧pending保持。实际页面已核Hard三图GT、邮件原文、候选/阅读/最终引用指标及产品导入入口。

WeMM在部署前已退出，按原unit恢复；主模型及BGE配置保持。产品静态构建沿用VITE_LEGACY_LOCAL_AUTH=1；首轮导出字段缺漏修复后继承已有答卷，没有重跑。最终六服务健康、测评空闲。单次install/resume/continue_smoke及局部发布脚本均已执行，不可重放。发布及推送证据保存在总工作区audit/20261117-atm-sources-implementation/。
