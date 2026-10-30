# PhotoBench 资源采集工具与固定版本

2026-10-29基线。Python运行依赖由 `apps/photobench/pyproject.toml` 的精确版本约束安装，原生工具和各设备环境基线见 [telemetry-tools.lock.json](../../apps/photobench/telemetry-tools.lock.json)。该文件只覆盖资源采集，完整产品环境继续使用部署流程的带哈希依赖锁。

## 采集来源

| 工具 | 固定版本或所属基线 | 用途 |
|---|---|---|
| psutil | **7.2.2** | CPU利用率、进程及部署进程检查 |
| nvidia-ml-py（导入名pynvml） | **13.615.71**，仅Linux x86_64安装 | NVML显存、GPU利用率、温度、功率 |
| macmon | **0.8.2** | Mac GPU/CPU活动、温度、功率；常驻pipe采样 |
| footprint、ioreg、vm_stat、sysctl、ps、lsof、libproc/IOKit | 本机macOS **26.6.1 / 25G76** | 分组驻留内存、整机GPU分配、系统内存与进程归属 |
| NVML、nvidia-smi | 153驱动 **595.99.02** | NVIDIA设备与进程显存；Python绑定版本与驱动版本分别记录 |
| tegrastats、nvmap | AGX L4T **36.4.7-20250918154033**；NX **36.4.3-20250107174145** | Jetson设备状态、GPU共享内存分配账本 |
| /proc、hwmon、procps、iproute2、lsof | 内核和发行包精确版本见锁文件 | PSS、RSS、进程身份、监听端口、CPU温度 |

当前实现没有引入jtop、asitop或powermetrics采集依赖。Mac原生工具随系统发布，Jetson工具随L4T发布；不为它们虚构独立的pip版本。表中的系统/驱动是各设备的已核验基线，跨设备无需强行改成同一版本；其他Mac设备单独记录其系统构建。

## 安装与版本保持

在新环境正常安装PhotoBench即可应用Python精确约束：

```bash
python -m pip install -e apps/photobench
python -m pip check
python -c 'from importlib.metadata import version; print("psutil", version("psutil"))'
# 仅NVIDIA Linux x86_64
python -c 'from importlib.metadata import version; print("nvidia-ml-py", version("nvidia-ml-py"))'
```

现有完整环境锁也须保留这两个版本；不通过覆盖运行中venv来同步。Jetson不需要安装NVML Python绑定。

Mac保留已核验的macmon 0.8.2二进制；锁文件记录本次Homebrew构建的SHA256。新机从受控制品恢复同一二进制并校验SHA，或单独构建0.8.2后记录新的制品SHA与来源。核对版本后可锁住Homebrew更新：

```bash
macmon --version             # macmon 0.8.2
brew list --versions macmon  # macmon 0.8.2
shasum -a 256 "$(command -v macmon)"
brew pin macmon
```

`brew pin`只保持已安装版本，不负责在新机解析旧版本。操作系统及驱动基线保存在部署镜像/系统快照中；升级时同时更新锁文件和部署记录。原生包可用 `dpkg-query -W nvidia-l4t-core nvidia-l4t-tools procps iproute2 lsof`，NVIDIA驱动用 `/proc/driver/nvidia/version`，Mac用 `sw_vers` 核对。

本轮仅修改依赖声明和文档并只读核版本；没有安装、升级、锁住设备包或重启服务。后续依赖升级应单独验证采集字段、单位和缺失值语义，保留旧run的原版本记录。

## 关键指标卡口径

首屏六张卡保留统一样式，新增显存卡与主模型内存并列。两张资源卡均显示有效采样峰值，单位GiB；旧数据缺字段保持缺失，不用显存替代内存，不补零。

- NVIDIA：主模型内存来自进程内存统计；主模型显存来自选定推理服务PID组的NVML分配。
- Jetson：主模型内存沿已记录的PSS/RSS版本；显存为该PID组nvmap分配。共享内存可能重叠，不将两卡数值相加。
- Mac：显示“整机显存占用”，采用IORegistry整机GPU分配采样峰值并标参考；当前没有可靠的主模型独占显存归属。GPU使用统一内存，可能与进程驻留内存重叠。

统计由已有采集/API字段提供，本轮不改变后端采集、公式、GT或历史数据。用户明确本轮前端/文档改动不跑回归测试，仅做静态检查和前端构建。
