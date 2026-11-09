"""``SystemdSentrixProcess``：用 ``systemctl --user`` 重启 2.0（S11 · T1103）。

**这是 ``SubprocessSentrixProcess`` 的同端口兄弟，不是它的替代品。** S9c 把「换模型 =
重启 2.0」隔离成端口 + 适配器，并在 ``infrastructure/deploy/sentrix_process.py`` 的模块
注释里逐条写下三条部署假设，其中第 2 条原话是：

    停一个「不是本进程起的」2.0 只能靠 ``lsof`` 按端口反查——那是尽力而为，不是权威。
    S11 若给主 API 配了 pidfile / systemd 单元，应换成读那一个。

S11 给主 API 配了 ``sentrix-api.service``（``systemd --user``），于是本模块就是那句
「换成读那一个」的落地：进程控制改走 ``systemctl --user``，运行端点的真相来源改读
**systemd 单元的有效配置**，而不是 ``lsof`` 反查。两种部署形态（同机裸进程 / systemd）
在迁移期并存——装配点按 ``Settings.sentrix_service_unit`` 选（空 = 裸进程，非空 =
systemd），见 ``bootstrap/container.py``。

**权威记录是什么**：``systemctl --user show <unit> --property=ActiveState
--property=Environment`` 的 ``Environment`` 里的 ``SENTRIX_VLM_BASE_URL``。
这是权威的，因为那正是 systemd 启动进程时喂给它的环境——不是 PhotoBench 自己写下的
一份可能与实际漂移的旁路记录。**端点也通过同一个来源写下去**：``restart`` 把
``Environment=SENTRIX_VLM_BASE_URL=<endpoint>`` 写进单元的 drop-in 覆盖文件
（``~/.config/systemd/user/<unit>.d/``），``daemon-reload`` 后 ``restart``。读写同源，
不存在「记录说 A、实际起的是 B」的分裂。**刻意不用仓库外的 EnvironmentFile**：单元在
仓库里（``deploy/systemd/``），drop-in 是运行时状态，两者互不覆盖。

**健康判据与 S9c 一致**：只看 ``GET /api/v1/health`` 回不回 ``ok`` / ``degraded``，
不看模型端点——2.0 自己不管理模型。``HEALTH_PATH`` 与 ``VLM_BASE_URL_ENV`` 从
``sentrix_process.py`` 导入而不是各写一份：那个环境变量名改错会让 2.0 静默退回默认
的 8100 端点（不报错、只是打错模型），**必须是单一定义**。
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from photobench.domain.deploy.ports import RestartOutcome
from photobench.domain.http.ports import JsonHttpClient
from photobench.domain.runtime.ports import RunRuntimeSnapshot
from photobench.infrastructure.deploy.sentrix_process import (
    HEALTH_PATH,
    VLM_BASE_URL_ENV,
    _snapshot_config_root,
)

#: systemctl 命令的参数超时。``daemon-reload`` / ``restart`` 正常在秒级返回。
_SYSTEMCTL_TIMEOUT = 120.0

#: drop-in 文件名。数字前缀让它在同目录其它覆盖文件里**最后生效**（systemd 按文件名
#: 字典序应用 drop-in，靠后的同名键覆盖靠前的）。
_DROPIN_NAME = "10-sentrix-vlm-endpoint.conf"


class CommandResult(NamedTuple):
    """一次外部命令的结果。``returncode`` 非 0 由调用方转成 ``RestartOutcome.error``。"""

    returncode: int
    stdout: str
    stderr: str


_SystemctlFn = Callable[[Sequence[str]], CommandResult]


def _run_systemctl(argv: Sequence[str]) -> CommandResult:
    """默认的命令执行器：不起 shell，把 OSError（二进制不在盘上等）也收成结果。

    **不抛**是刻意的：端口契约要求 ``restart`` 用 ``RestartOutcome`` 报失败，而不是
    用异常中断整轮评测。``systemd`` 不在盘上回 127（shell 的「command not found」约定）。
    """
    try:
        completed = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=_SYSTEMCTL_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(127, "", str(exc))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _default_config_home() -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME")
    return Path(raw) if raw else Path.home() / ".config"


def _environment_value(raw: str, name: str) -> str:
    """从 ``systemctl show -p Environment`` 那一行里取出某个变量。

    该行的形态是 shell 风格的 ``A=1 B="x y"``（systemd 自己做引号剥离，实测无空格时
    不带引号也不带引号），所以用 ``shlex`` 切；切不动就退回按空白切，**不抛**。
    """
    if not raw.strip():
        return ""
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()
    prefix = f"{name}="
    for token in tokens:
        if token.startswith(prefix):
            return token[len(prefix) :]
    return ""


class SystemdSentrixProcess:
    """``SentrixProcess`` 的 systemd ``--user`` 实现。"""

    def __init__(
        self,
        *,
        api_url: str,
        unit: str,
        http: JsonHttpClient,
        runtime_snapshot: RunRuntimeSnapshot | None = None,
        systemctl: _SystemctlFn | None = None,
        config_home: Path | None = None,
        vlm_env_var: str = VLM_BASE_URL_ENV,
        startup_timeout: float = 60.0,
        poll_interval: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        #: 命令执行器与时间源可注入。**不是为了「可测试」这个名头**：用例真去
        #: ``systemctl --user restart sentrix-api`` 会起停真机上正在跑的服务，并依赖
        #: 那台机器有没有这个单元。注入之后，用例钉的是**命令行参数**与**读回的值**。
        self._api_url = api_url.rstrip("/")
        self._unit = unit if unit.endswith(".service") else f"{unit}.service"
        self._http = http
        self._runtime_snapshot = runtime_snapshot
        self._systemctl = systemctl or _run_systemctl
        self._config_home = config_home or _default_config_home()
        self._vlm_env_var = vlm_env_var
        self._startup_timeout = startup_timeout
        self._poll_interval = poll_interval
        self._sleep = sleep
        self._clock = clock

    # ---- 端口实现 -----------------------------------------------------------

    def running_endpoint(self) -> str:
        """从单元的有效环境里读当前端点；单元没在跑就回空串。

        ``ActiveState`` 必须是 ``active``：单元死着时 ``show -p Environment`` 仍会回
        它「配置上」的值，照着它说「已经指着这里」会让一个崩掉的 2.0 永远不被重启——
        读存活与读配置缺一不可。
        """
        fields = self._show("ActiveState", "Environment")
        if fields.get("ActiveState") != "active":
            return ""
        if self._runtime_snapshot is not None:
            config_root = _environment_value(fields.get("Environment", ""), "PHOTOBENCH_CONFIG_ROOT")
            try:
                observed = json.loads((Path(config_root) / "runtime_connection.json").read_text())
                if any(
                    observed.get(key) != self._runtime_snapshot.connection().get(key)
                    for key in ("embedding", "product_settings")
                ):
                    return ""
            except (OSError, ValueError, AttributeError):
                return ""
        return _environment_value(fields.get("Environment", ""), self._vlm_env_var)

    def restart(self, *, endpoint: str, timeout: float = 60) -> RestartOutcome:
        """写 drop-in → ``daemon-reload`` → ``restart`` → 等健康。**任何一步失败都
        收进 ``RestartOutcome``，不抛。**

        ``restarted`` 的语义在这里是「**本次调用动过（或试图动）进程指向**」，与
        ``SubprocessSentrixProcess`` 一致：一旦决定要把 2.0 从 ``previous`` 换到
        ``target``，无论 ``systemctl`` 成没成，都不该再对外声称「端点本来就对」。
        调用方（``_sync_sentrix_endpoint``）正是按 ``restarted and not ok`` 判「换模型
        没成功但已经动过」→ 中断整轮，避免跑出「标着 A 模型、其实是 B 模型」的结果。
        """
        target = endpoint.strip()
        if not target:
            return RestartOutcome(
                ok=False,
                restarted=False,
                endpoint="",
                reason="no_endpoint_requested",
                error="endpoint is empty",
            )
        if "experience" in self._unit.lower():
            # 评测只能重启它自己的旧 8191；体验用户 API 各自由 systemd 按用户托管。
            return RestartOutcome(
                ok=False,
                restarted=False,
                endpoint=target,
                reason="experience_unit_refused",
                error=f"refusing to restart experience unit {self._unit}",
            )
        previous = self.running_endpoint()
        try:
            self._write_endpoint_dropin(target)
        except OSError as exc:
            return self._failed(target, previous, f"could not write systemd drop-in: {exc}")
        reload_result = self._systemctl(["systemctl", "--user", "daemon-reload"])
        if reload_result.returncode != 0:
            return self._failed(target, previous, _describe("daemon-reload", reload_result))
        restart_result = self._systemctl(["systemctl", "--user", "restart", self._unit])
        if restart_result.returncode != 0:
            return self._failed(target, previous, _describe(f"restart {self._unit}", restart_result))
        ok, error = self._await_health(timeout)
        return RestartOutcome(
            ok=ok,
            restarted=True,
            endpoint=target,
            previous_endpoint=previous,
            pid=self._main_pid(),
            reason="restarted",
            error=error,
        )

    # ---- 内部 ---------------------------------------------------------------

    def _failed(self, endpoint: str, previous: str, error: str) -> RestartOutcome:
        return RestartOutcome(
            ok=False,
            restarted=True,
            endpoint=endpoint,
            previous_endpoint=previous,
            reason="restarted",
            error=error,
        )

    def _show(self, *properties: str) -> dict[str, str]:
        """``systemctl show`` 的几个属性 -> dict。命令失败回空 dict（与「没有单元」同）。

        **坏输出当没有**——与 ``SubprocessSentrixProcess`` 对坏 pid 记录的口径一致：
        读不到真相时正确定向是「不知道」，于是下一次会尝试重启，而不是照着假记录跳过。
        """
        argv = ["systemctl", "--user", "show", *(f"--property={prop}" for prop in properties), self._unit]
        result = self._systemctl(argv)
        if result.returncode != 0:
            return {}
        fields: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                fields[key] = value
        return fields

    def _main_pid(self) -> int | None:
        """重启后主进程的 pid，读不到时 ``None``（``MainPID=0`` 表示没有主进程）。"""
        raw = self._show("MainPID").get("MainPID", "")
        return int(raw) if raw.isdigit() and raw != "0" else None

    def _await_health(self, timeout: float) -> tuple[bool, str]:
        """轮询 ``/api/v1/health`` 直到 200。``ok`` / ``degraded`` 都算起来了。"""
        deadline = self._clock() + timeout
        last_error = ""
        while self._clock() < deadline:
            try:
                body = self._http.request_json(f"{self._api_url}{HEALTH_PATH}", method="GET", timeout=5)
            except Exception as exc:  # noqa: BLE001 —— 还没起来就是连不上，继续等
                last_error = str(exc)
            else:
                if isinstance(body, dict) and body.get("status") in {"ok", "degraded"}:
                    return True, ""
                last_error = f"unexpected health payload: {body!r}"
            self._sleep(self._poll_interval)
        return False, last_error or f"sentrix did not become healthy within {timeout}s"

    def _write_endpoint_dropin(self, endpoint: str) -> None:
        """把端点写进 ``<unit>.service.d/10-sentrix-vlm-endpoint.conf``。

        systemd 的双引号语法：值里的 ``\\`` 与 ``"`` 要转义。URL 通常不含这两个字符，
        转义是给「不通常」留的正确性，不是保险丝。
        """
        path = self._dropin_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        values = {self._vlm_env_var: endpoint}
        if self._runtime_snapshot is not None:
            values["PHOTOBENCH_CONFIG_ROOT"] = str(
                _snapshot_config_root(
                    self._runtime_snapshot, endpoint, self._config_home / "photobench/startup-snapshots"
                )
            )
        lines = ["[Service]"]
        for key, value in values.items():
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'Environment="{key}={escaped}"')
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _dropin_path(self) -> Path:
        return self._config_home / "systemd" / "user" / f"{self._unit}.d" / _DROPIN_NAME


def _describe(action: str, result: CommandResult) -> str:
    detail = (result.stderr or result.stdout).strip().splitlines()
    reason = detail[-1] if detail else f"exit {result.returncode}"
    return f"systemctl {action} failed: {reason}"
