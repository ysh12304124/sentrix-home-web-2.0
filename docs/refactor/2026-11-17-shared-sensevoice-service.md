# 主机共享 SenseVoice 推理服务

## 代码现状与方案

PR #37 将模型放进用户 API，Container.build 构造 SenseVoiceRecognizer，main lifespan
调用 load/close。deploy/systemd/sentrix-experience-api@.service 和 run-user-api.sh
为每个账号启动 workers=1 的独立进程、独立数据根和令牌。49 个存活用户 API 因而可能
持有 49 个原生模型实例。共享文件路径只能复用文件缓存，不能复用 recognizer/session。

项目已有 WeMM、BGE、Chinese-CLIP HTTP 适配器，以及独立 sidecar 脚本、健康探测、
systemd unit 和 Mac 模型栈入口。沿用部署方管理进程、Container 按端口装配 HTTP 客户端、
状态端点显式报告故障的机制。嵌入客户端的空结果降级规则不适合语音，ASR 保持带码异常。
现有 8106 被图效用模型及一份 MOSS ASR 配置使用，本服务选择 8107。

## 实现与生命周期

用户 API 只装配 HttpSpeechRecognizer，启动 load 探测健康，close 关闭自身连接池。
用户进程没有模型加载回退，也没有子进程启动/停止逻辑。async 公开语音端点中的同步
健康探测交线程池执行。浏览器接口 schema 及录音、自动发送、失败草稿保护保持兼容。

部署管理员通过独立 systemd unit 管理每台主机一个 SenseVoice 服务，通过 scripts/deploy/start_sensevoice.sh 执行启动，Mac 模型栈由
start_local_models_mac.sh 按已有端口复用、nohup、日志、健康等待流程调用。服务启动命令先占用回环监听端口，再装配 FastAPI 与本地模型。
加载失败返回非零退出状态，systemd 按 on-failure 重启。重复同端口启动在模型加载前失败。
命令固定单进程，没有 reload/workers；主机上所有用户 API 指向同一个服务地址。
模型仍通过锁定 SHA 的准备脚本提前安装，生产统一准备在 /srv/sentrix/shared/models 下。
用户数据根独立，模型路径只由共享服务读取。服务进程退出才释放模型。

新增独立装配入口 bootstrap/speech_service.py，在可达性检查中登记为生产入口，
与实际 scripts.maintenance.sensevoice_sidecar 启动链对应。API 主进程无需 import 此工厂。

## 并发、故障与隔离

- 共享服务在读取正文前获取全局非等待锁，仅一个上传/解码在途。其他请求立即 429。
- Uvicorn HTTP 准入上限 16；极端过载可能为 503，无无界队列。健康检查独立于解码锁。
- 上传 30 秒上限，按采样率限制到 60 秒 PCM，模型校验采样率/帧对齐/数值范围。
- API 转发连接/池等待分别 2/1 秒、读写各 110 秒；浏览器已有 120 秒上限。
  POST 不自动重放。429、400、503/异常响应映射既有领域错误，前端保留文字草稿。
- 健康探测 2 秒超时与 2 秒缓存，失败可恢复；服务身份必须为 sentrix-sensevoice。
- 客户端取消不会卸载共享模型。已进入原生 decode 的任务通过 shield 继续，工作线程
  finally 释放全局准入锁。HTTP 取消后仍繁忙，直至原生推理结束。原生卡死需管理员重启。
- 服务只绑定回环、禁用访问日志。信任同机进程，与现有嵌入侧车部署边界一致。
  不持有用户账号、会话或数据库，逐请求独立解码 stream，没有音频持久化或结果查询接口。
  后端日志仅记录服务故障类型、时长等，不记录用户音频、转写、凭证。
- 停止任何用户 API 只关闭它的 HTTP 连接池，共享服务由 systemd 独立管理。

## 验证

- 后端语音原生适配器、HTTP 适配器、资源准备、公开端点、共享服务契约共 27 项通过。
- 真实模型多进程专项通过，零 skip。启动一个真实共享服务和三个独立用户 FastAPI
  进程，各自临时数据根，通过公开语音 HTTP 接口请求。各用户进程未导入 sherpa_onnx，
  服务 PID/instance_id 相同、model_loads=1，中文样例结果一致。关闭第一个用户 API 后，
  第二个用户仍能识别。重复服务启动在端口绑定阶段失败。
- 原生适配器单元测试直接断言同一实例多次 load/transcribe 只调用一次模型构造函数。
- 并发契约验证 busy 429、输入错误释放占用、health 可用、HTTP 取消后解码完成才释放。
- 前端语音及问答流 27 项、TypeScript、生产构建通过。
- 新增四个 Python 模块 strict mypy 通过；分层 11 个契约、模块/方法可达性均通过。
- capability-map 与公开 OpenAPI --check 通过。公开 schema 无变化。
- make ci 在主干既有 6 项 lint 失败处停止；模块地图仍有主干既有 4 项 request_context 缺项。

本次没有运行 49 个用户进程的压力测试，没有把模型权重文件大小直接当作物理内存节省。
三个真实 API 进程的共享实例与原生模块未加载检查证明了服务边界，49 用户部署仍需现场
验证连接吞吐、RSS/PSS 与系统内存。多个用户同时录音需要等待或重试 429，当前没有公平队列。
Linux systemd 文件已提供，尚未在远端主机安装；本地验证进程均由测试回收。

## 发布与回滚

部署步骤见语音运行手册。先准备权重、安装并启动共享 unit、确认服务身份健康，
再更新用户 API 并分批重启，确保旧 API 中已加载模型的进程退出。单纯切换发行目录不会释放
存活的旧实例。管理员独立管理共享服务，用户 API unit 无停止共享服务的依赖关系。
回滚恢复此前 API 进程内实现并重启相应 API，内存开销恢复至每进程一份。数据无迁移。

## 部署入口统一

模型准备逻辑和校验锁移动到 scripts/deploy/prepare_sensevoice.py 与
scripts/deploy/sensevoice-model.lock.json。共享 systemd 和 Mac 模型栈统一调用
scripts/deploy/start_sensevoice.sh，由它 exec sherpa-onnx 侧车进程。
移除此前添加的两个语音 Makefile 目标，部署文档统一使用 scripts/deploy 入口。
真实多进程测试同样通过该 shell 启动器拉起服务，验证实际部署链路。

部署入口修订验证通过了 30 项后端专项（含 3 项模型栈脚本启动/复用/错误服务拒绝），
通过 shell 启动器的真实多进程专项通过、零 skip；资源准备脚本校验复用本地模型，
启动脚本 bash 语法及准备脚本 strict mypy 通过。完整 Mac 模型栈以其他模型已运行的
替身进行脚本回归，未在本机重新拉起 VLM、BGE、CLIP 等整套模型，也未在远端安装服务。
