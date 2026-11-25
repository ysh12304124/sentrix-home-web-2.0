"""确认之后的第三步：给这个人采外观证据。

逐行移植 1.0 ``app.py:2022-2059`` 的 ``_analyze_confirmed_person_appearance``。
在 1.0 里它是 ``_refresh_confirmed_person`` 的第一件事，而后者是
``POST /api/face-clusters/{id}/confirm`` 的收尾。

**每人每事件取一张。** 这是这个用例全部的形态，也是它唯一不显然的地方：一个人在一个
事件里可能被拍到二十次，二十次都送模型既慢又不产生新信息——同一个下午、同一身衣服。
所以每个事件只挑**质量最高**的那一张（质量相同看检测置信度），跨事件累积。1.0 的 SQL
就是这个意思，2.0 收进 ``FaceRepository.best_instance_for_person_in_event``。

**一次失败不中断整批。** 图读不了、框退化、裁出来太小——1.0 把这些连同仓储错误一起
``catch`` 掉、继续下一张。2.0 **只 catch 前面那三种**（``OSError`` / ``ValueError``），
仓储抛出来的照常往上走。理由：``repositories/appearance.py`` 的三重守卫——尤其「这张脸
真的经 ``entity_mentions`` 确认给了这个人」——是 T85b 特意加的，把它一起吞掉，那条守卫
在生产路径上就永远看不见，而那正是本项目反复出现的「声称在检查、其实没有」。
媒体失败是**数据问题**（这张照片用不了），守卫失败是**代码问题**（有人在把 A 的衣服记到
B 头上），两件事不该共用一个 catch。

**模型不可用不是错误。** ``PersonVision`` 在模型不可用 / 图读不了 / 回复不是 JSON 时
一律返回 ``None``（见该端口的 docstring），这里跳过即可：外观证据是增强，不是确认的
前置条件。1.0 那条链路是「调用抛异常 → 被外层 catch」，2.0 是「调用返回 None → 这里
跳过」——净效果相同（这张照片不算数），机制换了，记在 ``s5-deviations.md``。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from sentrix.domain.identity.models import FaceInstanceRecord
from sentrix.domain.identity.ports import (
    AppearanceRepository,
    EntityRepository,
    FaceRepository,
    PersonCropRenderer,
    PersonVision,
)
from sentrix.domain.media.ports import ObservationRepository

# 确认 / 合并之后「给这个人采外观证据」的入口。生产装配的是入队（后台线程消费，见
# ``appearance_sampling``）；单测直接传 ``ObserveAppearance`` 同步跑。
RequestAppearance = Callable[[str], object]

# 1.0 ``model_clients.py:1352`` 的 ``normalize_confidence(..., 0.55)``。兜底值住在
# ``person_vision_model`` 里（那是解析回复的地方），这里只在回复形状不对时用它兜底。
DEFAULT_CONFIDENCE = 0.55


def _clothing(result: Mapping[str, object]) -> list[str]:
    """回复里的 ``clothing``。形状不对就是空表——空表已经在
    ``AppearanceRepository.list_for`` 那边有明确含义（「模型没能可靠归属给目标人物」），
    这里不另造一个第三种「不知道」。"""
    raw = result.get("clothing")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item).strip()]


def _confidence(result: Mapping[str, object]) -> float:
    raw = result.get("confidence")
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return DEFAULT_CONFIDENCE
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_CONFIDENCE


class ObserveAppearance:
    """给一个**已确认**的人补外观证据。返回真正落库的条数。

    返回值是给调用方和用例看的，不进 ``ConfirmResult``——那个类型只回答「确认成了
    谁」，条数是读侧的事（同 ``ConfirmResult`` 的 docstring）。
    """

    def __init__(
        self,
        *,
        faces: FaceRepository,
        entities: EntityRepository,
        observations: ObservationRepository,
        appearances: AppearanceRepository,
        crops: PersonCropRenderer,
        vision: PersonVision,
    ) -> None:
        self._faces = faces
        self._entities = entities
        self._observations = observations
        self._appearances = appearances
        # 裁剪是 IO（要 PIL），所以走端口；几何仍是 domain 的纯函数，
        # 实现里那一行只是把两者接起来。
        self._crops = crops
        self._vision = vision

    def __call__(self, person_id: str) -> int:
        recorded = 0
        for event_id in self._entities.event_ids(person_id):
            instance = self._faces.best_instance_for_person_in_event(person_id, event_id)
            if instance is None:
                continue
            # 已经采过、证据仍有效的脸不再送模型：确认之后再合并、或重复确认，只给新增的
            # 事件付那十秒。被驳回的证据不算采过（见 ``AppearanceRepository.is_sampled``）。
            if self._appearances.is_sampled(person_id, instance.id, self._vision.model_id):
                continue
            if self._observe(person_id, instance):
                recorded += 1
        return recorded

    def _observe(self, person_id: str, instance: FaceInstanceRecord) -> bool:
        # 1.0 ``app.py:2036`` 的原样：文件不在就跳过。它挡的是「库里有一行、
        # 磁盘上没有这个文件」——导入中断、媒体目录被搬过都会留下这种行。
        if not instance.asset_path or not Path(instance.asset_path).is_file():
            return False
        try:
            path, crop_bbox = self._crops.render(instance.asset_path, instance.bbox)
        except (OSError, ValueError):
            # 读不了 / 框退化 / 裁出来不够大。三种都是这张照片的问题，不是这个人的问题。
            return False
        try:
            result = self._vision.analyze_person_appearance(path, self._metadata(instance))
        finally:
            # 预览图/裁剪件从不作为 Asset 或长期文件保存（``person_vision`` 的
            # docstring）。**放在 finally**：模型调用抛出去时也不能把临时文件留在盘上。
            Path(path).unlink(missing_ok=True)
        if result is None:
            return False
        self._appearances.record(
            person_id=person_id,
            face_instance_id=instance.id,
            crop_bbox=crop_bbox,
            clothing=_clothing(result),
            confidence=_confidence(result),
            # 1.0 从模型客户端的返回值里取 ``model``（``:1350``），2.0 从端口上取——
            # 端口的 docstring 写明这一条，理由是「这次请求的是哪个模型」不该由
            # 一次回复的内容决定。字节相同、来源不同。
            model_name=self._vision.model_id,
        )
        return True

    def _metadata(self, instance: FaceInstanceRecord) -> dict[str, object]:
        """1.0 ``app.py:2046-2050`` 的**三个**字段。

        这三个键会 ``json.dumps`` 之后拼进提示词（``person_vision_model`` 的
        ``analyze_person_appearance``），所以**多一个键就是改了模型看到的输入**。
        1.0 只有这三个，这里也只有这三个。

        一处可忽略的差异照实记下：1.0 的 ``target_face_bbox`` 是库里那串 JSON 原文，
        这里走的是解析过的 ``instance.bbox``。检测器产出的框本来就是浮点，两种写法
        ``json.dumps`` 出来一样；只有手工插进去的整型框会有 ``0`` 与 ``0.0`` 的区别，
        而那种行不来自生产路径。
        """
        observation = self._observations.get(instance.observation_id)
        captured = observation.captured_at if observation is not None else None
        return {
            "face_instance_id": instance.id,
            "target_face_bbox": list(instance.bbox),
            "capture_time": captured.isoformat() if captured is not None else None,
        }
