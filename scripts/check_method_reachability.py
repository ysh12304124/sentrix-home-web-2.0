#!/usr/bin/env python3
"""端口方法级可达性：**每个端口方法都要有生产调用者**。

## 为什么另起一条，而不是改 ``check_reachability.py``

那条问的是「**这个模块**是不是生产代码」，它答得对，而且 ``PENDING_WIRING`` 的双向
纪律（登记了但真接线了就报红）值得原样保留。这条问的是下一层的问题：「**这个方法**
有没有人调」。两个问题不同，判据也不同，混进一个脚本会让两边都变难改。

## 为什么存在

``check_reachability.py`` 的判定单位是模块名。于是 ``repositories/facts.py`` 因为
``container.py`` 构造了 ``SqliteFactRepository`` 就算「已接线」——**它里面 ``list_facts``
有没有人调，那个脚本从没问过**。2026-09-29 的审计正是从这个缝里找出五个零调用方法：

    FactRepository.list_facts / set_status / supersede_others
    RelationshipRepository.confirm / retract

它们的共同形态是「**读路径写成死代码**」：写侧接好了、读侧写好了、没人接。
而这种东西**不会报错**，只会让某个界面永远空着。

## 判据：唯一性代替类型推断

模块级能收紧成「这个名字是从那个模块 import 进来的、而且被读用过」，因为模块级有
**import 绑定**这个锚。方法级没有：``self._faces.list_clusters()`` 是**实例属性的
属性访问**，要知道 ``self._faces`` 是 ``FaceRepository`` 得靠类型推断——那是 mypy 的活，
不是一次 AST 走查能做的。

退一步的可行判据是「**这个名字在所有端口里唯一，且在别处被属性访问过**」。
实测（2026-09-29）：136 个端口方法里 **120 个名字唯一**，16 个重名。五个已知的
零调用方法里，**五个都可查**（``list_for`` 与 ``create`` 落在重名那边，查不了）。

**唯一性的方向性要说清**：它挡的是**误报**，不是漏报。一个唯一名字若恰好在非端口类上
也被用了，这条会漏掉那个死方法——**漏报是可接受的，误报会让门禁被关掉**。
所以判据宁松勿严，重名的 16 个**打印出来**当作已知盲区，而不是假装它们不存在。

## ``PENDING_METHODS`` 是例外表，不是后门

一个端口方法可能是「本切片先落地、下一个切片才接线」。这类必须逐个登记并写明
**哪个任务会接线**；同时另有一条反向规则：登记里的方法一旦真被调用了，检查会报红
要求删掉它。与 ``check_reachability.py`` 的 ``PENDING_WIRING`` 是同一条纪律。
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = REPO_ROOT / "apps" / "api" / "src"

# 端口方法 -> 会接上它的任务。**每个条目都必须写明任务**，否则它就是一个说不出理由的
# 例外，而说不出理由的例外名单会把这条不变量重新变成摆设。
#
# 前四条来自 2026-09-29 的覆盖审计（人工查出来的）；**后十条是这条门禁第一次跑就报出来的**
# ——模块级判据看不见它们（那些模块因为别的方法被用了而算「已接线」）。它们分两类：
#
#   - 能归到本计划任务上的，写任务号；
#   - 归不上的，写「S6 · 依赖核查」。这不是含糊其辞：按 S5c 的 T86g 体例，
#     动工前先核依赖、核出缺口再写任务定义，**核查本身就是一个有交付物的任务**。
#     把它们硬塞进一个编号会更像有归属，实际只是把问题推后。
PENDING_METHODS: dict[str, str] = {
    # VideoJob 工作流把旧的「清空派生物后重跑」改为保留产物并续跑。该仓储方法仍保留，
    # 供未来显式的管理员清理流程复用；普通重处理不得调用它或删除已有产物。
    "VideoRepository.cleanup_derivatives": (
        "持久 VideoJob 上线后无生产调用者；待显式管理员清理流程定义后接线"
    ),
    # —— S7b2 T311：运行表读写 + 模型边清场，T312/T313/T314 接线 ——
    #
    # 与 S7b1 的 T302 同一条路：这几个方法只有 ``FamilyGraphService``（T312）与
    # ``POST /family-graph/runs``（T313）、批末触发（T314）会调。S7b1 刻意没搬它们
    # （「到达不了的分支不搬」），S7b2 的调用者到了才落——落下的这一刻仍没有调用者，
    # 所以照例登记，等接线时由反向规则自己删。**T312/T313 已按它删掉四条**
    # （``supersede_model_relationships`` / ``update_run`` / ``latest_run``
    # 由反向规则报红；``create_run`` 门禁**没报**——人工按纪律删的）。（``get_run`` 没进这张表：它已经被
    # ``create_run`` / ``latest_run`` / ``update_run`` 这三处 ``self.`` 调用用到了，
    # 门禁认这个。）
    # **这张表在 S7b1 里被清空过一次。** T302 落了家庭图谱端口（9 个方法）与标签
    # 白名单模块，按「先落地、后接线」登记在这里；随后 T303 接上模块（`check_reachability`
    # 那条）、T305 接上四个读方法、T306 接上五个写方法——**每一步都是这张表自己报红
    # 要求删的**，删到不剩。与 S6 的 `PhotoInspector.inspect` 走的是同一条路。
    # —— 审计查出（2026-09-29）——
    # facts 的三条（list_facts / set_status / supersede_others）已在 T113/T114 接线，
    # 按反向规则从这张表里删掉。**这三条是被自己删掉的，不是被人想起来的**——
    # 接线完成的那一刻这条门禁就报红要求删，那正是它存在的形式。
    # RelationshipRepository.retract 与 .list_person_relationships 在 S7a · T203
    # （关系端点）接线，按反向规则删掉——**又是两条被门禁自己删掉的登记**。
    # 同一次接上的还有 .create / .confirm（这两个的名字在多个端口重名，落在本门禁的
    # 盲区里，从来就没进过这张表）与新加的 .list_relationships。
    # —— 本门禁首跑查出 ——
    # EntityRepository.maintain_property 在 T127（非人物实体抽取）接线，按反向规则删掉。
    # **它是被 T127 接上的，不是被 T128 接上的**——抽取要写 label/category/时间那几个属性。
    # list_properties（读）在 T128a 接线，按反向规则删掉——私密地点的投影要读实体的
    # ``private_flag`` / ``alias`` 两个属性（``domain/identity/decoration.py``）。
    # **又一条被门禁自己删掉的登记**：接线那一刻它就报红要求删。
    # **S7a 核查（2026-10-02）发现这条登记的出处引错了**：s5-deviations 第 15/35 条
    # 讲的是 `graph_violations` 算完即弃，与 `dissolve` 无关（那两条已在 S7a 了结，
    # 结论是「随 person_graph.py 判死」）。`dissolve` 本体是 **1.0 的重分段**留下的：
    # `ConfirmedPersonEvents.resegment` 走的是 `events.merge`，一次都没调过它。
    # 所以它现在**没有归属**，不是「等 S7」。留在这张表里是因为它确实零调用者。
    "EventRepository.dissolve": "归属未定（S7a 核查：resegment 走 merge，没用到它）",
    "PortraitRepository.list_for_person": "S6 · 依赖核查（画像版本列表，归属未定）",
    "PortraitRepository.set_lock": "S6 · 依赖核查（画像手改/锁定，归属未定）",
    # ObservationRepository.list_by_asset 在 T119（条件排除）接线，按反向规则删掉。
    #
    # PhotoInspector.inspect 与上面那些**来历不同**：它不是被审计或本门禁查出来的疏漏，
    # 而是计划**要求**先落地、后接线的一件（§4 Task 123/124：先落 inspector 端口 + 适配器
    # + 它的对照用例，再 T123、再 T124），所以它落地的那一刻必然没有生产调用者。
    # 登记在此，等两条工具接上时**由反向规则自己删掉**——与 facts 那三条走的是同一条路。
    "MediaStorage.path_for": "S6 · 依赖核查（媒体路径解析，归属未定）",
    "LexicalIndex.rebuild_scope": "S6 · 依赖核查（词法索引重建，归属未定）",
    # AssetRepository.update_status 在 S8a · T504 接线（POST /videos/{id}/reprocess
    # 重置状态），按反向规则删掉——**又是被门禁自己删掉的一条**。
    #
    # —— S9a（2026-10-04）：PhotoBench 的模型运行时端口 ——
    #
    # `photobench/domain/runtime/ports.py` 的三个协议是 S9a 新落的，它们的生产调用者
    # 是 S9b 的编排链（T911–T913）。与 `PhotoInspector.inspect` 是同一个来历：计划
    # 要求先落地端口、后接线。
    #
    # **十四减五等于九，这个差额本身要记下来**：`LifecycleProvider.start` / `.stop`、
    # `InferenceProvider.list_models`、`TelemetryProvider.gpu_stats` / `.process_memory`
    # 这五个名字**不在下面**——不是它们已经接线了，而是本门禁的判据是「这个名字在别处
    # 被属性访问过」，而 photobench 自己的实现里就有 `self.start()` / `self.stop()` /
    # `self.list_models()` / `self.gpu_stats()` / `self.process_memory()`。也就是说这五条
    # 是本门禁文档里写明的**盲区**（漏报可接受），不是被接线了。
    #
    # —— S9b-1（2026-10-04）：复查那五个盲区 ——
    #
    # 计划书要求「接上真调用者后要复查这五个是否真的可达」。逐条复查的结果：
    #
    #   - `InferenceProvider.list_models` —— **现在真可达了**：`ModelProfiles._query_current_model`
    #     调它（`application/model_profiles/service.py`）。它不在下面，是因为它**已经**
    #     被接线，不是因为盲区。本切片新加的用例 `test_current_model_reads_the_served_model_
    #     off_the_endpoint` 用假件把它钉住了（断言假件的 `calls` 里有 `list_models`）。
    #   - `LifecycleProvider.start` / `.stop`、`TelemetryProvider.gpu_stats` /
    #     `.process_memory` —— **仍然是盲区，一个真调用者都没有**。它们的生产调用者是
    #     1.0 的 run-execution 主链（`_phase_model_deploy` 起停模型、`GpuSampler` 采样），
    #     属 S9b-2。本门禁对它们**依然漏报**：改坏这两组方法的名字不会有任何用例变红。
    #     这一条如实登记，别读成「已接线」。
    #
    # —— S9b-2（2026-10-04）：五个盲区里三个不再是盲区 ——
    #
    # S9b-2 的 run-execution 主链接上了 `LifecycleProvider.start` / `.stop`
    # （`_phase_model_deploy`）与 `TelemetryProvider.gpu_stats` / `.process_memory` /
    # `.system_memory`（`GpuSampler`）。**但那五个名字仍然不在下面这张表里**——
    # 它们是被本门禁的盲区放过的，本切片接上它们**不会**产生任何删除动作。
    # 证据在 `apps/photobench/tests/test_run_execution.py` 与 `test_gpu_sampler.py`：
    # 改坏 `start` / `stop` / `gpu_stats` / `process_memory` / `system_memory`
    # 的名字或签名，那些用例会红。**盲区本身没有修**（判据仍是「名字被属性访问过」，
    # 而 photobench 自己的实现里有 `self.start()` 之类），修它要换判据，不在本切片。
    #
    # 下面这三条是**被反向规则要求删掉的**：它们真的接上了生产。
    #   - `InferenceProvider.chat` —— `_probe_model_endpoint`（模型就绪与健康检查）
    #   - `InferenceProvider.chat_stream` —— `_model_probe`（TTFT / 吞吐探测）
    #   - `TelemetryProvider.system_memory` —— `GpuSampler.sample_once`
    #
    # —— S9b-1 新落的读写端口 ——
    # `InferenceProvider.health` **已被反向规则删掉**：2026-10-15 起 `_probe_endpoint_health`
    # （入 QA 前的端点守卫，1.0 `benchmark_orchestrator.py:3394`）真的调它。登记里原先
    # 写的理由是「端点探活走的是 `chat`」——那句只证明「这一句能通」，而守卫问的是
    # 「现在值不值得开一轮 487 题」，是另一件事。
    "InferenceProvider.token_count": "S9b-2 余项（提示词预算，主链未用到）",
    "TelemetryProvider.kv_cache": "S9b-2 余项（KV cache 指标，采样器从 process_memory 里取）",
    # `InferenceProvider.capabilities` / `LifecycleProvider.profiles` / `.state` 三条
    # **已被本门禁的反向规则自己删掉**：S9b-1 的 `/api/profiles` 与 `/api/current-model`
    # 真的调了它们（`application/model_profiles/service.py`），登记随即被报红要求删除。
    # 与 `PortraitRepository.list_for_person` 那些条目走的是同一条路。
    #
    # `read_json` / `write_json` / `path_for` / `iter_entries` / `load_state` /
    # `read_reviews` / `read_gpu_sample_lines` / `metadata_by_qa_id` 同理，落地即接线，
    # 从不进这张表。
    #
    # `request_bytes` 是另一半：端口落地了、调用者还没到（资产代理那条路由要改成 2.0 的
    # `/api/v1/assets`，属 S9c 的端点适配）。照纪律登记，等 S9c 接上时由反向规则删。
    #
    # —— S9b-3（2026-10-04）：`request_bytes` 被它自己的反向规则删掉了 ——
    #
    # 证据判分（`application/run_execution/judge.py::_inline_images`）要按 asset_id
    # 取回图片字节做 data URL，它调的就是 `JsonHttpClient.request_bytes`。**又是被这张表
    # 自己报红要求删的一条**。顺带记一条本切片新出现的盲区：`RunResultWriter.save_reviews`
    # 与 `.delete_run` 这两个名字在 `RunSuites` 上也有同名方法，于是落进下面的重名盲区
    # ——它们的真实调用者是 `RunSuites.save_reviews` / `.delete_run`（S9b-3 的写侧路由），
    # 用例在 `apps/photobench/tests/test_suites.py`。**别把盲区读成已接线**。
}


def iter_protocols(src: Path) -> Iterator[tuple[Path, str, list[str]]]:
    """产出 (文件, 协议类名, [方法名])。只看 ``domain/**/ports.py``。"""
    for path in sorted(src.rglob("domain/**/ports.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any("Protocol" in ast.unparse(base) for base in node.bases):
                continue
            methods = [
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_")
            ]
            yield path, node.name, methods


def shadowing_names(src: Path) -> set[str]:
    """**会让属性访问解析到非端口对象**的方法名。

    ## 这个集合是怎么定出来的（两次修正）

    **第一版**只看端口之间的重名。``confirm`` 判成唯一——可它撞上了
    ``ReviewCluster.confirm``（``application/identity/review_cluster.py``），
    而全仓只有那两处 ``.confirm(`` 调用点，都不是仓储的。判据于是把
    ``RelationshipRepository.confirm`` 判成「已接线」，**反向规则随即要求删掉一条
    正确的登记**。所以「唯一性只漏报、不误报」是错的：**漏报会被反向规则放大成误报**。

    **第二版**要求全源码唯一。**更糟**——协议声明一次、实现类再定义一次，每个端口
    方法天然至少两个定义者，149 个方法名全被判重名，检查直接空转。

    **第三版（本版）**：撞名只可能来自**能把属性访问解析到端口之外的那些类**。
    实现类不继承协议（结构化类型），所以按继承识别行不通；但可以按位置分：

    - ``domain/**/ports.py`` 里的协议 —— 方法本来就该在这；
    - ``infrastructure/`` 下的类 —— 是端口的实现，同名不是撞名；
    - **其余位置**（``application/`` / ``api/`` / ``bootstrap/`` / ``domain`` 非 ports）
      的类若定义同名方法，才是真正的撞名来源。

    实测：这一版的盲区从 149 降到与第一版同量级，而 ``confirm`` 这类误判被挡住。
    """
    shadows: set[str] = set()
    for path in src.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(src)
        parts = relative.parts
        # **按「in parts」判，不按 parts[0]。** 调用方传的 ``src`` 是 ``apps/api/src``，
        # 于是 ``parts[0]`` 是包名 ``sentrix`` 而不是 ``infrastructure``——写成
        # ``parts[0] == "infrastructure"`` 时这条判断**永远不成立**，实现类（如
        # ``SqliteFactRepository.list_facts``）就全被当成遮蔽来源，136 个方法名一起
        # 变盲区、检查空转，而输出读起来完全正常。
        if "infrastructure" in parts:
            continue
        # 同理不用 ``Path.match("domain/**/ports.py")``：``**`` 在 match 里不展开成
        # 「任意层」，实测不命中 ``domain/memories/ports.py``。
        if path.name == "ports.py" and "domain" in parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    shadows.add(item.name)
    return shadows


def attribute_names(src: Path) -> set[str]:
    """生产代码里所有被读用的属性名（``x.foo`` 的 ``foo``）。"""
    used: set[str] = set()
    for path in src.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                used.add(node.attr)
    return used


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=str(DEFAULT_SRC))
    parser.add_argument(
        "--pending",
        action="append",
        default=[],
        metavar="CLASS.method=TASK",
        help="临时登记一个待接线的方法（测试用；真实登记写在本文件的 PENDING_METHODS 里）",
    )
    args = parser.parse_args()
    pending = dict(PENDING_METHODS)
    for item in args.pending:
        method, _, task = item.partition("=")
        pending[method] = task

    src = Path(args.src)
    used = attribute_names(src)

    owners: dict[str, list[str]] = defaultdict(list)
    for path, klass, methods in iter_protocols(src):
        for method in methods:
            owners[method].append(f"{path.relative_to(src)}::{klass}")

    # 撞名的两种来源：端口之间重名，以及被非端口类遮蔽（理由见 ``shadowing_names``）
    shadows = shadowing_names(src)
    collided = {name for name, where in owners.items() if len(where) > 1} | (shadows & set(owners))

    problems: list[str] = []
    for name, where in sorted(owners.items()):
        if name in collided:
            continue  # 重名 -> 判据不成立，进盲区（下面统一打印）
        if name in used:
            # 反向规则：登记了但真接线了，登记必须删掉
            for owner in where:
                key = f"{owner.rsplit('::', 1)[-1]}.{name}"
                if key in pending:
                    problems.append(
                        f"{key}: 已经接上生产了，但 PENDING_METHODS 里还留着它的条目"
                        f"（登记的接线任务是 {pending[key]}）——删掉这条"
                    )
            continue
        for owner in where:
            key = f"{owner.rsplit('::', 1)[-1]}.{name}"
            if key not in pending:
                problems.append(
                    f"{key}: 端口方法没有任何生产调用者（全仓没有一处属性访问用到这个名字）"
                    f"——接线，或写进 PENDING_METHODS 并注明哪个任务会接"
                )

    for problem in problems:
        print(problem)
    print(f"method-reachability: {len(problems)} 个问题")
    # 盲区要打印出来，不能假装不存在——否则「0 个问题」会被读成「全都查过了」。
    print(
        f"已知盲区：{len(collided)} 个方法名在多个端口重名，唯一性判据不成立，本检查不覆盖："
        f"{'、'.join(sorted(collided))}"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
