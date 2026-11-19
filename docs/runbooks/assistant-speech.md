# 助手主机共享语音服务

`/assistant` 的浏览器只负责录音，SenseVoice-Small 由主机独立 ASR 服务执行，多个用户 API 经内部 HTTP 复用同一推理实例。
手动点击“停止并识别”后，将识别文字追加到现有草稿并自动发送；
服务器接受提问后清空草稿，失败或状态未确认则保留文字，不自动重试。
60 秒超时只填回草稿，需用户确认后发送。

## 准备和启动

首次迁移安装后端依赖并准备模型：

```sh
.venv/bin/pip install -e "apps/api[dev]"
.venv/bin/python scripts/deploy/prepare_sensevoice.py
```

权重准备完成后，通过项目现有的模型栈部署入口启动：

```sh
scripts/deploy/start_local_models_mac.sh
# 既有兼容入口也会进入同一模型栈
# scripts/deploy/start_legacy_mac.sh
```

脚本按既有流程检查模型端口、启动后台进程、写入 logs 并等待健康，再启动用户 API。
SenseVoice 步骤位于图效用模型之后、API 之前，使用 sherpa-onnx，默认端口 8107。
部署方只需单独启动 SenseVoice 时，使用同目录的模型服务启动器：

```sh
scripts/deploy/start_sensevoice.sh
```

这个启动器供模型栈以 nohup 调用，Linux 上由共享 systemd unit 调用，前台 exec
同一个 sherpa-onnx 服务。已运行的健康服务由模型栈直接复用。开发前端另开终端运行
`make web-dev`。独立 `make dev` 的用户 API 连接已启动的共享模型服务。

准备脚本将锁定的官方模型安装到 `SENTRIX_DATA_DIR/models/sensevoice-small`，
默认 `data/models/sensevoice-small`。部署服务可以通过启动命令的 `--model-dir` 指定已准备的目录。
模型为 2024-07-17 SenseVoice-Small INT8，Python sherpa-onnx 固定 1.12.40，
CPU provider、2 个推理线程、自动语种、逆文本规范化。
1.12.20 的浏览器 WASM 版本已移除；其 Python wheel 不适用于当前开发环境。

`scripts/deploy/sensevoice-model.lock.json` 锁定来源、压缩包和逐文件 SHA-256；
已有文件通过校验后不再下载。模型、token、许可和官方中文测试音频保存在数据目录，不提交 Git。
离线准备可用：

```sh
.venv/bin/python scripts/deploy/prepare_sensevoice.py /path/to/official-model.tar.bz2
```

共享服务启动时加载一次模型，每段请求创建独立识别 stream。用户 API 的
`Container.build()` 只构造 `HttpSpeechRecognizer`，启动 `load()` 只探测 HTTP 健康，
关闭 `close()` 只释放自己的连接池。共享服务由部署方管理，用户退出或重启后继续运行。
服务先绑定回环端口，再装配和加载模型。同端口重复启动会先报占用错误。
启动命令固定单进程，部署启动统一经过 scripts/deploy/start_sensevoice.sh，运行期不要用 uvicorn 的多 worker/reload 包装该工厂。

服务缺少权重或加载失败会报错并以非零状态退出。启动阶段不联网下载。
用户 API 在服务不可用时仍可启动，语音状态明确提示，识别请求返回 503。
客户端健康探测 2 秒超时，缓存 2 秒，服务恢复后重新探测。用户 API 始终经共享服务推理。

## 模型端口顺序

按照现有 Settings、侧车入口和模型栈脚本的默认端口继续分配，SenseVoice 使用 8107。

| 端口 | 模型服务 | 现有依据 |
|---|---|---|
| 8100 | 主 VLM | Settings.vlm_base_url |
| 8101 | BGE-M3 | Settings.text_embedder_url |
| 8102 | WeMM | Settings.wemm_url |
| 8103 | Chinese-CLIP | Settings.legacy_clip_url |
| 8104 | BGE reranker | bge_reranker_sidecar.py 默认端口 |
| 8105 | Qwen3-ASR | start_local_models_mac.sh 的 ASR_PORT |
| 8106 | 图效用 VLM；另有 MOSS ASR 部署使用此端口 | UTILITY_VLM_PORT / sentrix-demo-moss-asr.service |
| 8107 | 新增 SenseVoice-Small 主机共享服务 | speech_service_url / sensevoice_sidecar.py / systemd unit |

已有 8106 两套部署需按各自主机配置运行，不能同时占用同一监听地址。
体验部署中的 8113 是 Chinese-CLIP 的端口覆盖，正式默认仍为 8103，参见 experience-ports.md。
本轮按默认模型服务连续序列顺延，客户端、启动入口、systemd、健康探测、环境样例统一使用 8107。

## Linux 多用户部署

沿用 `sentrix-experience-api@.service` 的每用户独立数据根和进程隔离。
每台主机由部署管理员安装一次 `deploy/systemd/sentrix-sensevoice.service`，
以已有的 `sentrix-models` 账号运行。该 unit 与用户 API unit 独立，服务管理器统一负责
启动、退出和故障重启。无需在 49 个用户目录各准备一份模型。

准备与启用命令由部署管理员执行，路径与现有 `/opt/sentrix/current` 发行结构保持一致：

```sh
# sentrix-models 账号应已按其他模型侧车的部署方式创建。
sudo install -d -o sentrix-models -g sentrix-models /srv/sentrix/shared
cd /opt/sentrix/current
sudo -u sentrix-models env SENTRIX_DATA_DIR=/srv/sentrix/shared \
  .venv/bin/python scripts/deploy/prepare_sensevoice.py
sudo install -m 0644 deploy/systemd/sentrix-sensevoice.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sentrix-sensevoice.service
curl --fail http://127.0.0.1:8107/health
```

所有用户 API 使用 `SENTRIX_SPEECH_SERVICE_URL=http://127.0.0.1:8107`（默认值），
用户自身 `SENTRIX_DATA_DIR` 保留原值。更新发行代码后分批重启各用户 API，
使旧的进程内模型真正退出。仅更新目录或文件无法卸载仍在运行的旧实例。
服务更新时由管理员独立重启 `sentrix-sensevoice.service`，期间语音暂不可用，草稿保留。
用户 API 的 shutdown 不向共享服务发送停止命令。

本机 Mac 完整模型栈入口 `start_local_models_mac.sh` 会启动/复用 8107 并等待健康。
重复启动由绑定端口在模型加载前阻止；单独 `make dev` 只连接服务。

## 接口与资源边界

- `GET /api/v1/assistant/speech/status` 返回 `{available,message}`。
- `POST /api/v1/assistant/speech/transcribe?sample_rate=48000` 接收
  `application/octet-stream` 的单声道 little-endian float32 PCM，返回 `{text}`。
- AudioWorklet 保留实际采样率，后端接受 8–192 kHz、0.2–60 秒音频；
  校验帧对齐、时长、有限采样值和 [-1,1] 范围。sherpa-onnx 负责重采样。
  上传按采样率限制大小（最高约 46 MB），流式读取限时 30 秒。
- 共享服务在读取上传正文前统一准入，一个在途上传/解码，其他请求返回 429。
  原生识别在线程池执行。HTTP 总连接/任务准入上限 16，极端过载由 Uvicorn 返回 503。
  服务没有等待队列和自动重试。原生推理不支持硬中止，若卡死需管理员重启服务。
- API 连接/连接池等待分别限时 2/1 秒，转发读写各限时 110 秒。浏览器状态查询限时 10 秒，识别请求限时 120 秒。取消或会话切换会中止
  HTTP 请求并丢弃晚到结果；已进入原生推理的请求不能强杀，会完成后释放并发槽。
  客户端超时不会卸载模型，后续请求可能短暂收到忙碌提示。
- 音频会从手机/浏览器传到运行 API 的本机，内存处理，不落盘、不写音频或转写日志、
  不发往云端 ASR。文本继续走原有问答 API。沿用同源会话、CSRF 与内部调用守卫。
  共享服务仅绑定回环地址，信任边界为同机可信进程，与既有嵌入侧车一致。
  不把 8107 通过控制代理对外暴露。每次请求独立 stream，没有跨用户会话、音频文件、
  结果查询接口或转写日志。服务仅健康诊断报告 PID/实例标识/成功加载次数。
- 浏览器不再下载模型或依赖 WASM SIMD、ASR Worker；仍需 HTTPS 和 AudioWorklet。
  旧浏览器资源准备命令、模型锁文件与识别 Worker 已删除，保留录音 AudioWorklet。
  从旧分支升级时可删除旧生成目录 `apps/web/public/speech/sensevoice-1.12.20`，
  避免旧大文件进入 Vite 构建产物。

### 手机局域网 HTTPS

Vite 使用 `vite-plugin-mkcert` 自动下载 mkcert、安装本机根 CA，并为 localhost 和
当前局域网 IP 生成证书。证书按插件默认位置保存在用户缓存目录，私钥不进入项目。
首次启动需要网络，安装系统信任可能要求输入系统管理员密码；之后复用证书。
无需手工创建 `.certs/dev.pem`，无需进入临时 worktree。

先按上文启动共享语音服务，再在另外两个终端启动 API 和前端：

```sh
make dev       # 后端 http://127.0.0.1:8191
make web-dev   # 前端 https://localhost:4274（在另一个终端运行）
```

全新克隆先安装前端依赖 `npm --prefix apps/web ci`，后端模型资源按上文准备。
前端代理 `/api`、`/control` 到原来的后端地址；手机打开 Vite 输出的 HTTPS Network 地址。
第一次切换 HTTPS 时先停止原来的 HTTP Vite 进程，4274 被占用时会明确报错。
构建不运行证书安装，Vitest 配置也明确排除 mkcert 插件。

手机必须安装并信任根 CA。本配置使用插件默认目录，根证书为
`~/.vite-plugin-mkcert/rootCA.pem`（插件显式以该目录作为 CAROOT）。
iPhone 安装描述文件后，在“设置 → 通用 → 关于本机 → 证书信任设置”开启完全信任；
Android 在系统安全设置安装 CA 证书，然后使用 Chrome 访问。
只传 `rootCA.pem`，不要传根 CA 私钥。插件不能远程替手机安装信任。

官方说明：https://github.com/liuweiGL/vite-plugin-mkcert#mobile-devices


## 验证

```sh
.venv/bin/pytest apps/api/tests/unit/test_sensevoice.py apps/api/tests/contract/test_speech.py
.venv/bin/python scripts/run_real_tests.py apps/api/tests/integration/test_sensevoice_real.py
cd apps/web
npx vitest run src/features/assistant/speech
npx tsc -b --force
npm run build
```

真实模型专项启动一个独立共享服务和三个独立用户 API 进程，各自使用独立临时数据根，
通过公开语音 API 识别官方中文样例。验证服务 PID/实例标识一致、model_loads=1、
每个用户进程未导入 sherpa_onnx、结果一致，关闭一个用户 API 后服务仍运行。
同端口重复启动必须在加载前失败。已有单元测试另验证原生构造函数只调用一次。
这项验证没有实际启动 49 个用户进程，也不将模型文件大小视为节省的物理内存数值。
真人麦克风和实体手机仍需验收。
浏览器验收应检查手动停止自动发送、60 秒只填回草稿、取消丢弃结果、失败保留草稿，
网络面板无旧模型/WASM 下载，仅有本机状态查询和 PCM 上传。

官方来源与许可：
[模型下载与说明](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html)、
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)、
[SenseVoice](https://github.com/FunAudioLLM/SenseVoice#license)。
