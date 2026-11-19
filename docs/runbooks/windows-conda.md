# Windows Conda 开发环境

此配置用于 Windows x86_64 / NVIDIA 本地开发。Python 3.11 沿用项目开发基线；
主 API、PhotoBench 和开发工具安装在同一个独立环境，两个前端分别使用已有 npm 锁文件。
修改代码前仍须完整阅读 [模块接口与硬规则](../architecture/module-interfaces.md)。

## 创建与激活

从仓库根目录执行：

```powershell
conda env create --file environment.windows.yml
conda activate sentrix-home-web-2.0

# insightface 的 CPU ORT 与 GPU ORT 共用文件：GPU 包必须最后覆盖安装。
python -m pip install --no-deps --force-reinstall onnxruntime-gpu==1.30.0
python -m pip check

npm.cmd --prefix apps/web ci
npm.cmd --prefix apps/photobench/web ci
```

本机 Conda 根目录为 `H:\anaconda`，环境为
`H:\anaconda\envs\sentrix-home-web-2.0`。PowerShell 尚未加载 Conda hook 时，
可以只为当前终端加载，不必修改全局配置：

```powershell
(& 'H:\anaconda\Scripts\conda.exe' shell.powershell hook) | Out-String | Invoke-Expression
conda activate sentrix-home-web-2.0
```

环境设置 `PYTHONNOUSERSITE=1`，避免混入系统 Python 的用户包。编辑器选择此环境的
`python.exe`。现有 Makefile 和 Bash 部署脚本使用 `.venv/bin/*` 及 Unix 工具，
Windows Conda 下直接运行对应的 `python -m ...` / npm 命令。

## 平台依赖

- `cn-clip` 固定依赖 `lmdb==1.3.0`，其 pip 发布缺少本环境需要的 Windows wheel；
  先由 Conda 安装 `python-lmdb=1.3.0`，保留上游精确版本。
- API 的 ONNX Runtime 平台声明未覆盖 Windows，因此本环境显式固定 CPU/GPU 两个
  发行包为 `1.30.0`，并在创建后最后重装 GPU 包，沿用仓库已有的命名空间处理方式。
  以后重新安装依赖时也要检查这两个版本及 GPU 推理，`pip check` 无法验证文件归属。
- PyTorch / torchvision 固定 CUDA 13.0 构建；CUDA 能否使用以本机实际算子执行为准。
- Conda FFmpeg 构建被本机应用程序控制拒绝执行。2026-11-18 已通过 WinGet 安装
  `Gyan.FFmpeg` 9.0.2，并将允许执行的 `ffmpeg.exe` / `ffprobe.exe` 放入 Conda
  环境根目录供服务查找；实际视频时长探测与 PNG 抽帧成功。未修改系统执行策略。
- PhotoBench 已使用 portalocker 的非阻塞独占租约；Windows 文件与目录持久化刷新已适配，锁竞争和持久化失败仍显式报错。
- mypy 使用官方源码安装（`--no-binary=mypy`），避免依赖被策略拦截的编译扩展；不降低 strict 检查规则。
- 原生依赖检查期间，scikit-learn、`torch/lib/shm.dll` 和
  `ml_dtypes/_ml_dtypes_ext` 曾被应用程序控制策略拦截。已固定兼容的 ONNX 1.17.0，
  避免其新版本对 `ml_dtypes` 的加载依赖；真实 buffalo_l 人脸推理取得 512 维向量。
  本机人脸明确使用 CPUExecutionProvider；这不是 ONNX GPU 推理验收。
- 环境兼容性相关 135 项回归通过；另有 Windows API 路径和目录刷新专项。完整门禁仍未通过：类型检查报告 111 项错误，主前端测试 29 项失败，主 OpenAPI 存在漂移；grimp 原生扩展被策略拦截，完整覆盖率未获得。

## 环境验证

在激活环境后，从仓库根目录执行：

```powershell
python --version
python -m pip check
python -c "import sys, torch, onnxruntime; print(sys.executable); print(torch.__version__, torch.version.cuda, torch.cuda.is_available()); print(onnxruntime.__version__, onnxruntime.get_available_providers())"
python scripts/deploy/runtime_acceleration_probe.py --help
python -m pytest apps/api/tests/contract/test_health.py
npm.cmd --prefix apps/web run build
npm.cmd --prefix apps/photobench/web run build
ffmpeg -version
ffprobe -version
```

PhotoBench API 验证命令为
`python -m pytest apps/photobench/tests/test_api_routes.py`；此命令已包含在兼容性回归中通过。

上述检查验证依赖、基础接口和构建；provider 清单本身不算真实 GPU 推理证据。
它们也不替代代码变更要求的完整日常门禁、≥85% 覆盖率与真实资源专项。
后续改代码仍按硬规则同步端口、失败语义、契约与登记，并执行适用的完整验收。

2026-11-17 本机已验证：`pip check` 无依赖冲突、环境禁用用户 site、PyTorch CUDA
矩阵乘法与卷积通过、两个前端类型检查及生产构建通过。完整项目门禁、真实模型与
真实相册专项未通过或未执行，不宣称完整就绪。本机检查产物保存在忽略目录
`data/environment/`，包括 `acceleration.json`、两次接口检查的 JUnit、
`requirements.observed.txt` 和 `conda-explicit-win-64.txt`；观察快照含本机路径，
不是跨平台依赖锁。创建环境以仓库中的 YAML 和本说明为准。

## 开发入口

先按项目部署文档准备模型、权重、数据及对应 HTTP 侧车，再启动主 API：

```powershell
python -m uvicorn --factory sentrix.main:create_app --host 127.0.0.1 --port 8191
```

PhotoBench 后端启动命令为
`python -m uvicorn --factory photobench.main:create_app --host 127.0.0.1 --port 8772`。

主前端本地开发沿用 README 的直连 8191 配置：

```powershell
$env:SENTRIX_PREVIEW_TARGET = 'http://127.0.0.1:8191'
$env:VITE_LEGACY_LOCAL_AUTH = '1'
npm.cmd --prefix apps/web run dev -- --port 4274
```

环境安装不包含模型权重、GeoNames、真实相册或 Judge 密钥；这些资源按对应部署与
真实环境验收文档准备。启动时的依赖检查与降级报告继续遵守现有产品规则。

模型与数据准备清单见 [本地所需资源](local-required-assets.md)；修复语义及未完成门禁见 [Windows 修复记录](../refactor/windows-environment-deviations.md)。

## 本机已配置入口（2026-11-18）

### 2026-11-19 原生 llama 迁移阻碍

用户要求停止使用WSL部署llama。忽略脚本 `data/model-runtime/start-service.ps1`
的 `vlm` 入口现改为Windows原生CUDA、独立进程、持久日志及有界就绪检查。
官方b12503 Windows CUDA12.4程序包与cudart包已下载至该目录，SHA256均与
GitHub官方release的digest一致，解压至 `llama-b12503-cuda`。
实际启动退出码为 `0xC0E90002`；Windows CodeIntegrity事件3077记录
`llama-server-impl.dll` 不满足Enterprise签名要求，Smart App Control阻止加载。
原生CUDA包与已有Vulkan包的该DLL SHA256同为
`c5fbd51cab1545c92c6b15f4787d8e68277f1a1ad49dba798c50ee6cc5dc1ea7`。
因此当前原生模型未启动、8100未就绪，不能开始测评；需要系统管理员提供获准的
原生构建或相应放行策略。未更改系统防护策略，未重新启用WSL。

此前WSL实例的实际退出日志为 `CUDA error: an illegal memory access was encountered`，
调用栈包含 `mtmd_helper_decode_image_chunk`、`llama_context::decode` 和
`ggml_backend_cuda_synchronize`，随后进程退出；这不同于HTTP等待预算耗尽，
也未证明WSL自身是底层CUDA错误的原因。迁移后的文本、图片和并发验收尚未执行，
全量487题尚未通过。旧运行在模型掉线后保存的processed状态不保证观察内容完整，
恢复后应使用新scope验证全链路。

2026-11-19 后续恢复：用户明确授权并手动关闭智能应用控制后，只读核对其状态为0。
上述同一官方CUDA包成功启动原生 `llama-server.exe`，8100直接由Windows进程监听，
不再通过WSL。`-Service vlm` 使用独立隐藏进程、独立stdout/stderr日志和就绪检查；
命令退出不会按脚本逻辑终止模型进程。专项脚本
`data/environment/native-model-acceptance.py` 以合成图片执行3轮16并发混合请求，
24个图片请求和24个文本请求全部HTTP成功且回答非空；原生function工具调用参数
验证通过，16槽存在，报告保存在同目录JSON。8191/18223的模型、嵌入和数据库
健康检查及8210 ready通过。该短时验收不代表长时间多模态稳定性或487题全量通过；
先前模型掉线期间构建的scope不可直接视为完整记忆，重新评测应新建full运行。

- 产品登录：`http://localhost:8210`，使用原有账号与密码；独立产品 API 为 8191。
- PhotoBench：`http://127.0.0.1:8772`；独立评测 API 为 18223，避免与产品媒体混用。
- 本机忽略配置 `data/model-runtime/start-service.ps1` 支持 `-Service product`、
  `control`、`photobench`、`benchmark`、`bge`、`clip`、`vlm`。
  先启动三个模型服务，再启动产品、控制面与 PhotoBench；已有监听服务时不要重复启动。
  `benchmark` 通过既有 Container 装配的进程端口仅启动评测 API、登记真实 PID，
  不创建测评。正式测评仍按冻结配置核验并在必要时重启 API，不手工伪造 PID 记录。
- VLM 在 WSL Ubuntu-22.04 使用 llama.cpp b12503 CUDA 12.8、RTX 4090；所需 Ubuntu
  24.04 运行库放在独立目录，通过专用加载器启动，不替换宿主系统库。
- 经用户同意取消原 C1 运行后，本机改为 QA 并发 16、评测 API 执行线程 16、
  模型槽 16；模型总上下文 262144、每槽 16384，Flash Attention 与 q8_0 KV 缓存，
  RAM 缓存 2048 MiB。16 个同时短文本请求全部 HTTP 200，观测到 16 槽实际并行；
  这只验证并发启动与短请求稳定性，不能代表完整多模态 QA 负载验收。
  启动过的 C16 运行已保存并停止。按用户最新要求，服务保持待机，由用户手动启动
  新 scope 的全链路 487 题测评，重新导入、处理并按当前代码构图；不使用旧图复用模式。
- 明确选择 legacy：BGE-M3 文本 1024 维、Chinese-CLIP 图片 768 维；人脸为 CPU。
  GeoNames 压缩包已放入两个运行数据根的 `geo` 目录。
- 原始账号库与旧媒体数据库保留。原账号库损坏，已复制原账号及密码哈希到
  `data/local-control-windows/control.db`，完整性检查通过；未新建账号或重置密码。
  媒体绑定已改回产品 `data`、评测 `data/photobench-sentrix`，历史结果根为
  `apps/photobench/results`；不再使用最初临时创建的空数据根。
- 经用户授权，将原评测 media 的 5912 个文件实际复制到产品 media，两根使用不同
  物理副本。修复 Mac 路径时为每条资产保留独立受管文件名；同一根中的相同不可变
  内容可以有多个硬链接文件名，产品和评测根之间不建立硬链接。
- 原评测 SQLite 有真实损坏。官方 `.recover` 导出的 SQL 无法完整导入，不能算成功；
  改为只读逐表恢复可读行，并与完整产品库合并，重建 FTS 后结构完整性检查通过。
  损坏原库及替换前数据库完整备份至 `data/backups`，详细恢复缺口在
  `data/environment/row-recovery-result.json`，不能宣称全部历史逻辑记录都已恢复。
- 产品现有 13812 条资产记录，其中 13743 条有本机文件；69 条旧测试图或派生帧缺失，
  保留记录并登记在 `data/environment/final-data-bindings.json`，不伪造媒体。
  评测库另保留本轮临时运行的数据，共 14518 条；历史列表加载完成，共 16 条记录。
  图片原文件/缩略图、视频 Range、文本原文及原账号登录均实际检查通过。
- 数据集 `album3-max-video10` 的 334 图、10 视频和 487 QA 文件均在。
  独立一图一题验收完成真实导入、记忆构建、问答、豆包 Judge（2/2）和结果落盘；
  产品页面也完成上传后的实际问答。专项证据放在 `data/environment` 与本机评测结果根。
  此结果不替代完整 CI、全量 QA、资源采样覆盖率或模型质量验收。

2026-11-18 分页/遥测验证：PhotoBench 前端 765 项、分页读取 52 项、资源采集 143 项通过。
Qwen 聊天与看图适配器 66 项通过；本机测试须设置 `NO_PROXY=localhost,127.0.0.1,::1`，
并显式使用当前已部署的 legacy embedding（8101/8103），避免代理将本机探针变成 502，
或默认 wemm 探针访问未部署的 8102。未跳过启动探针，未降低断言。
Windows NVML 包为 `nvidia-ml-py==13.615.71`，实际采到 CPU、物理内存、整卡显存、
GPU 利用率、温度及功率；Windows/WSL 模型 PSS/进程 GPU 归属仍缺失。
已有历史曲线空值不可回填；新版页面显示本轮保存的来源故障。
下一次评测 API 启动使用 runtime_connection.json 中 `qwen35_nonthinking` 配置，
保留原 8 步/6 次工具与证据门禁，未自动重跑正式评测。main 对照与验收限制详见
[修复记录](../refactor/windows-environment-deviations.md)。

## BGE CUDA 部署（2026-11-19）

2026-11-19 晚间重启：本机忽略启动脚本的 PhotoBench 入口显式设置既有
`PHOTOBENCH_IMPORT_TIMEOUT=86400`，用于新运行的同步流水线预算；QA 并发仍为16。
此前全链路运行在7200秒读超时后失败，产品后台继续执行，历史失败记录及快照保留。
恢复模型后重新启动8191、8210、18223和8772，模型文本请求、两个API健康端点、
控制面ready、数据集清单和16个空闲模型槽均已检查；这不代表全量487题或CI已通过。
重新验证应新建full运行，旧运行续跑仍继承原7200秒预算，不能用它验证新预算。

BGE 侧车支持 `--device cpu|cuda`，默认 CPU 兼容 Mac；本机 RTX 4090 使用：

```powershell
conda activate sentrix-home-web-2.0
python scripts/maintenance/bge_text_sidecar.py --port 8101 --device cuda
```

本机忽略启动脚本的 bge 入口也已显式添加 `--device cuda`。不变更 PowerShell 执行策略；
使用该脚本时沿用可正常执行本地脚本的 PowerShell 7。CUDA 请求不可用时 health 为错误，
不回退 CPU。权重仍为 FP32，最大512 token、attention-mask mean pooling 和 L2 归一化保持；
模型、输入和 mask 在相同设备，推理用锁限制并发显存峰值。GPU执行位置记录在健康运行事实中。
CPU/GPU 独立512-token对照为0.515/0.070秒、最大绝对差约2.98e-7；不能推断整条导入同倍提速。
实现指纹随代码变化，新的正式评测使用新 scope 重建并冻结身份，不篡改旧运行快照。

本机CUDA部署已于2026-11-19 10:32生效：BGE 8101 PID127392、评测API18223 PID29528、
PhotoBench8772 PID27832。GPU健康检查确认device=cuda、FP32、1024维，临时8104验证服务
已停止。旧CPU run 20261119-092805-atm-bench-media-current-ad49e5 在批次边界取消，
2500封已入库并索引记录和历史结果保留。新建全链路 run：
20261119-103226-atm-bench-media-current-0961db，使用新scope、50封批次及向量组原子提交，
共6742封、381QA。此处登记的是实际部署与任务启动，不代表381题已经完成或通过。
