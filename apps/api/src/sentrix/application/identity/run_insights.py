"""人物画像流水线：五个阶段跑完一个记忆空间。

移植 1.0 ``backend/person_insights.py:152-255`` 的 ``PersonInsightService``，
按裁决① 只保留五个阶段（``rank`` / ``select`` / ``moments`` / ``compile`` / ``write``），
``infer_graph`` 与它带来的 ``state["graph"]`` 整个不搬。

**阶段顺序是数据流，不是风格。** ``select`` 要用 ``rank`` 挑出来的核心人物，
``moments`` 要用 ``select`` 挑出来的代表照（模型看到的就是那几张），``write`` 要用
``compile`` 的证据包。1.0 用一个固定的 ``STAGES`` 元组把顺序写死，2.0 直接用调用顺序
——但那条依赖是真的，改动顺序会让某个阶段拿到空输入而不报错。

**与 1.0 最大的结构差异：没有 run 表。** 1.0 的 ``run()`` 绕着
``person_insight_runs`` 转——状态机（queued/running/completed/failed）、
``current_stage`` 断点续跑、``stats["moment_failures"]`` 的落点。0006 没建那张表
（``0006_identity.sql:234``），所以这里是一个**同步跑完五个阶段的函数**：
没有 run_id、没有续跑、失败数作为返回值给调用方。

代价照写：**一次失败就是整趟失败**。1.0 那个 ``try/except`` 会把异常写进 run 表然后
返回一个 ``failed`` 的 run 对象，调用方看得见哪一步断的；2.0 的异常直接往外抛，调用方
只知道自己这一趟没成。**这是本切片明确接受的**（计划里「缺陷 3 的日志落点」那件事），
换来的是不必为一个只有一条写入者的状态机建表。

**1.0 的两个缺陷边界，这里都不碰**：

- ``run()`` 开头那段「``status == 'running'`` 就拒绝」的并发守卫，没有 run 表就没有
  对象可守——2.0 不做并发保护（同 ``import_album`` 的 docstring：本来就是串行重活）。
- ``except Exception`` 把所有阶段错误吞成 ``failed`` 状态：2.0 不吞，见上。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sentrix.application.identity.extract_moments import ExtractMoments
from sentrix.application.media.model_generation import GenerationGuard, unguarded_generation
from sentrix.domain.identity.insights import (
    CORE_PEOPLE_LIMIT,
    REPRESENTATIVE_LIMIT,
    REPRESENTATIVE_PER_EVENT,
    rank_core_people,
    select_representatives,
)
from sentrix.domain.identity.models import (
    PORTRAIT_SOURCE_DETERMINISTIC,
    PORTRAIT_SOURCE_MODEL,
    PORTRAIT_TRIGGER_PIPELINE,
)
from sentrix.domain.identity.portrait import (
    PERSON_PORTRAIT_PROMPT_VERSION,
    compile_portrait_evidence,
    deterministic_portrait,
    validate_portrait,
)
from sentrix.domain.identity.ports import (
    EntityRepository,
    FaceRepository,
    MomentRepository,
    PersonVision,
    PortraitRepository,
    RelationshipRepository,
)

# 1.0 ``person_insights.py:249`` 的 ``getattr(self.gamma, "model", "unknown")`` 兜底。
UNKNOWN_MODEL = "unknown"


@dataclass(frozen=True, slots=True)
class InsightRun:
    """一趟的结果。1.0 把这些放进 run 表的 ``stats`` 里；2.0 没有那张表，直接返回。

    ``portraits`` 数的是**真正落库的版本**——校验连续三次不过的那几个不算。
    """

    ranked: int
    core: int
    selections: int
    moments: int
    moment_failures: int
    portraits: int


class RunInsights:
    def __init__(
        self,
        *,
        faces: FaceRepository,
        entities: EntityRepository,
        moments: MomentRepository,
        relationships: RelationshipRepository,
        portraits: PortraitRepository,
        extract_moments: ExtractMoments,
        vision: PersonVision,
        generation_guard: GenerationGuard = unguarded_generation,
    ) -> None:
        self._faces = faces
        self._entities = entities
        self._moments = moments
        self._relationships = relationships
        self._portraits = portraits
        self._extract_moments = extract_moments
        self._vision = vision
        self._generation_guard = generation_guard

    def __call__(self, scope_id: str) -> InsightRun:
        with self._generation_guard(scope_id):
            return self._execute(scope_id)

    def _execute(self, scope_id: str) -> InsightRun:
        ranked = rank_core_people(self._faces.person_candidate_features(scope_id), limit=CORE_PEOPLE_LIMIT)
        # ``tier`` 由 ``rank_core_people`` 判（1.0 ``:203`` 的写法）：排进前 N 且跨够
        # 天数的才算「核心人物」。非核心的仍留在 ``ranked`` 里——1.0 把它们留在
        # ``state["ranked"]`` 供别的阶段看，2.0 只把条数带回结果。
        core = [item for item in ranked if item.get("tier") == "core"]

        selections = self._select(core)
        extraction = self._extract_moments(scope_id=scope_id, selections=selections)
        packs = self._compile(scope_id, core)
        portraits = self._write(core, packs)
        return InsightRun(
            ranked=len(ranked),
            core=len(core),
            selections=len(selections),
            moments=extraction.moments,
            moment_failures=extraction.failures,
            portraits=portraits,
        )

    def _select(self, core: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """每个核心人物挑最多 12 张代表照（每事件最多 2 张）。1.0 ``:256-283``。

        **``vector_by_asset`` 传空表，与 1.0 一字不差**（``:271`` 的
        ``select_representatives(candidates, {}, ...)``）。这意味着
        ``select_representatives`` 里那段「与已选中的任一张余弦 ≥ 阈值就跳过」的**去重
        分支在生产上从不执行**——1.0 唯一的调用点传的就是字面量 ``{}``，而 2.0 没有
        第二个调用点。

        **照搬，不自己接上。** 判据：这里与 ``selection_json`` 那条断线（第 135 条）
        形状不同——那条是键名写错、写入端明明在读它；这条是调用方**显式**递了一个空表，
        读起来像一个选择（参数留着、值给空）。证据不足以断定它「本意要接」，所以照搬，
        把发现记在 ``s5-deviations.md`` 第 137 条。规则本身在 ``test_insights.py`` 里有
        直接用例（T86d 的 19 条定点变异包含它），所以它**不是没测**，是**生产不走**。
        """
        selections: list[dict[str, Any]] = []
        for item in core:
            person_id = str(item["person_id"])
            picked = select_representatives(
                self._faces.representative_candidates(person_id),
                {},
                limit=REPRESENTATIVE_LIMIT,
                per_event=REPRESENTATIVE_PER_EVENT,
            )
            for selection in picked:
                # 候选 SQL 没有取 ``fi.observation_id``（1.0 原样），所以要回查一次实例。
                instance = self._faces.get_instance(str(selection["face_instance_id"]))
                selections.append(
                    {
                        "asset_id": selection["asset_id"],
                        "face_instance_id": selection["face_instance_id"],
                        "person_id": person_id,
                        "cluster_id": selection.get("cluster_id"),
                        "observation_id": instance.observation_id if instance else None,
                        "event_id": selection.get("event_id"),
                        # ``select_representatives`` 刚写上去的那包（第 135 条接上的线）。
                        "selection_json": selection.get("selection_json"),
                    }
                )
        return selections

    def _compile(self, scope_id: str, core: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        """给每个核心人物收一个证据包。1.0 ``:218-222``。

        2.0 的 ``compile_portrait_evidence`` 收的是**材料**（人 / 瞬间 / 关系），不是
        1.0 那个 ``store``——按端口取，不越过仓储直接查库。
        """
        packs: dict[str, dict[str, Any]] = {}
        for item in core:
            person_id = str(item["person_id"])
            person = self._entities.get(person_id)
            if person is None:
                continue
            packs[person_id] = compile_portrait_evidence(
                person,
                self._moments.list_for(person_id=person_id, scope_id=scope_id),
                self._relationships.list_for_entity(person_id, scope_id),
            )
        return packs

    def _write(
        self,
        core: Sequence[Mapping[str, Any]],
        packs: Mapping[str, Mapping[str, Any]],
    ) -> int:
        """写画像：模型 → 校验 → 重写 → 校验 → 确定性兜底 → 校验 → 落库。

        1.0 ``:225-254``。**三次校验各带自己的 ``source``**：前两次是 ``model``，
        兜底那次是 ``deterministic``——而这正是 2.0 修掉的缺陷 2（1.0 的兜底画像通不过
        自己的校验，因为它按模型的标准去量一段模板文案）。校验函数在 T86c 里改了签名
        收 ``source``，所以这里的调用必须把「这段文本是谁写的」如实传进去，否则兜底
        永远过不了、画像永远落不了库，而失败是静默的（``continue``）。
        """
        written = 0
        for item in core:
            person_id = str(item["person_id"])
            pack = packs.get(person_id)
            if not pack:
                continue
            portrait = self._vision.write_person_portrait(pack)
            ok, _ = validate_portrait(dict(pack), portrait or {}, source=PORTRAIT_SOURCE_MODEL)
            if not ok:
                portrait = self._vision.write_person_portrait(pack)
                ok, _ = validate_portrait(dict(pack), portrait or {}, source=PORTRAIT_SOURCE_MODEL)
            if not ok:
                portrait = deterministic_portrait(dict(pack))
                ok, _ = validate_portrait(dict(pack), portrait, source=PORTRAIT_SOURCE_DETERMINISTIC)
            if not ok or portrait is None:
                # 1.0 原样：三次都不过就跳过这个人，不落库、不报错。
                continue
            self._portraits.create(
                person_id,
                portrait_text=str(portrait.get("portrait_text") or ""),
                prompt_version=PERSON_PORTRAIT_PROMPT_VERSION,
                themes=_themes(portrait),
                evidence_refs=_evidence_refs(portrait),
                trigger_type=PORTRAIT_TRIGGER_PIPELINE,
                model_name=self._vision.model_id or UNKNOWN_MODEL,
            )
            written += 1
        return written


def _themes(portrait: Mapping[str, object]) -> list[dict[str, object]]:
    """画像里的主题表。走到这里它一定已经过了 ``validate_portrait``（主题是列表、
    每条是带 ``title``/``summary``/``evidence_refs`` 的字典），窄化只是让类型对得上，
    不是重新校验一遍。"""
    raw = portrait.get("themes")
    return [dict(theme) for theme in raw if isinstance(theme, Mapping)] if isinstance(raw, list) else []


def _evidence_refs(portrait: Mapping[str, object]) -> list[dict[str, object]]:
    """把每个主题的 ``evidence_refs`` 摊平。1.0 ``:245-248`` 的两层列表推导。"""
    refs: list[dict[str, object]] = []
    for theme in _themes(portrait):
        items = theme.get("evidence_refs")
        if isinstance(items, list):
            refs.extend(dict(item) for item in items if isinstance(item, Mapping))
    return refs
