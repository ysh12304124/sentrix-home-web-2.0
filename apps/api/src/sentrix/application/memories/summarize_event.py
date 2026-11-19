"""事件总结：确定性摘要 + 一次模型调用。

对应 1.0 ``pipeline.py:636-691`` 的 ``summarize_event`` / ``_persist_event_summary``
与 ``model_clients.py:1394-1433`` 的 ``GammaClient.summarize_event``。

三段结构原样保留：

1. 事件没有观察、或模型端点不可用 → **原样返回**，不写任何东西（1.0 ``:637-639``）；
2. 模型返回的不是 dict、或 title 还是占位符（``待总结事件`` / ``待确认的家庭记录`` /
   ``待判断``）→ 用**确定性摘要** ``fallback_projection`` 覆盖（1.0 ``:658-664``）；
3. 模型调用抛异常 → 同上，用确定性摘要（1.0 ``:668-670``）。

`fallback_projection` 是本文件里唯一值得逐字保留的东西：1.0 那段注释写着「有些本地模型
会把结构化契约的回复解码成一个裸字符串」，所以它不是「模型不好用时的兜底」，而是
**跑批时的正常路径之一**——整册导入里绝大多数事件本来就没有模型总结。它完全确定性，
所以进 ``make ci``。

**与 1.0 的一处明确差异**：1.0 的 ``_persist_event_summary`` 同时写一条 ``episodic``
事件向量（走 8101 文本嵌入），给它后面的「按事件语义找图」用。**1.0 消费它的是
``text_ann`` 通道**——``TextAnnRetriever`` 的 ``spaces=("semantic", "episodic")``
（``backend/retrieval/text_ann.py:30``），两个 space 的命中按余弦取大之后**合并成同一条
通道**再进融合，不是两条通道。2.0 的 ``text_ann`` 属 S6，S4 接进 ``fuse()`` 的三路
（visual_ann + lexical + metadata）**没有任何一路读 ``episodic``**，写进去只是一条
没人消费的向量；等 S6 落地 ``text_ann`` 时连同两个 space 一起搬。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from sentrix.domain.assistant.models import ChatMessage, ChatRole, ModelReply
from sentrix.domain.assistant.ports import ChatModel
from sentrix.domain.media.models import Observation
from sentrix.domain.media.ports import AssetRepository, ObservationRepository
from sentrix.domain.memories.events import EventParticipantRecord, EventRecord
from sentrix.domain.memories.ports import EventParticipantRepository, EventRepository

log = logging.getLogger("sentrix.memories.summarize_event")

# 1.0 ``:660``：这三个是占位标题，模型原样返回它们等于没总结。
PLACEHOLDER_TITLES = frozenset({"待总结事件", "待确认的家庭记录", "待判断"})

# 1.0 ``:655``：确定性摘要的固定置信度。
FALLBACK_CONFIDENCE = 0.45
FALLBACK_MODEL = "deterministic_event_fallback"

# 1.0 ``:666``：模型那条路给出的默认置信度（``normalize_confidence(..., 0.5)``）。
MODEL_CONFIDENCE = 0.5

# 1.0 在确定性摘要里当噪声剔掉的值（``:645``）。
NOISE_VALUES = frozenset({"待判断", "未识别", "未知"})

OTHER_PLACE = "其他或不确定"

_SUMMARY_PROMPT = """你是家庭事件总结器。下面是一组已经按拍摄时间和地点聚类的图片观察，
可能同时包含与视频场景时间对齐的语音转写。
视觉事实只能使用给定图片观察；语音转写只能支持其中确实说出的内容，不能据此臆测画面里的人物、物体或动作。
不得把元数据地点以外的信息当作事实。
**人物**：`participants` 里列出的是**已经确认身份**的人，可以直接使用他们的姓名
（例如「王建国和张晓莉在正定古城合影」）；`participants` 之外的人**一律不得猜测姓名**，
只能按画面描述（「一位穿黑色外套的女士」）。观察文本里的「一名男子」「一个孩子」
不是身份，不要自行给它安名字。
如果观察彼此不足以支持具体事件，使用保守、描述性的标题。
地点必须使用观察中的语义地点（例如餐厅、家中厨房、湖边、商场），禁止输出 GPS 坐标、
经纬度、文件名或路径。事件总结必须综合全部 observations，不能只依据其中一张图片。
严格返回简体中文 JSON：title（不超过 20 字）、event_type、activity、
summary（包含时间范围、语义地点、人物和可验证活动）、confidence。\n事件："""


@dataclass(frozen=True, slots=True)
class EventProjection:
    title: str
    event_type: str
    activity: str
    summary: str
    confidence: float
    model: str


def fallback_projection(
    event: EventRecord,
    observations: Sequence[Observation],
    participants: Sequence[EventParticipantRecord] = (),
) -> EventProjection:
    """确定性事件摘要。逐行对应 1.0 ``pipeline.py:641-654`` 的 ``fallback_projection``。

    同一批观察永远给出同一个标题——这正是它进 ``make ci`` 的理由。
    """
    place = str(event.place or OTHER_PLACE).strip()
    # **已确认参与者的姓名**。没有它，摘要只能说「一名男子在…」——而这些人是谁
    # 系统其实已经知道（`event_participants` 由身份确认动作写入）。
    names = list(dict.fromkeys(item.person_name for item in participants if item.person_name))
    who = "、".join(names[:3])
    activities: list[str] = []
    captions: list[str] = []
    event_types: list[str] = []
    for observation in observations:
        for value, target in (
            (observation.activity, activities),
            (observation.caption, captions),
            (observation.event_type, event_types),
        ):
            text = str(value or "").strip()
            if text and text not in target and text not in NOISE_VALUES:
                target.append(text)
    activity = "、".join(activities[:3]) or "图片记录"
    event_type = event_types[0] if event_types else "家庭活动"
    title = (
        f"{who}{place}{activity}"
        if who and place != OTHER_PLACE
        else (f"{who}{activity}" if who else (f"{place}{activity}" if place != OTHER_PLACE else activity))
    )
    body = "；".join(captions[:4] or activities[:4])
    summary = f"{who}在{place}，记录了{body}" if who else f"{place}记录了{body}"
    return EventProjection(
        title=title[:20],
        event_type=event_type[:20],
        activity=activity[:20],
        summary=summary[:240],
        confidence=FALLBACK_CONFIDENCE,
        model=FALLBACK_MODEL,
    )


def projection_from_reply(reply: str, *, event: EventRecord) -> EventProjection | None:
    """模型回复 → 投影；拿不到可用投影时返回 ``None``（由调用方退回确定性摘要）。

    1.0 那两处「不是 dict 就退回」与「title 还是占位符就退回」在这里合并成一个判据：
    **要么给出一个可用的投影，要么什么都不给**，中间的半成品没有第三种处理。
    """
    parsed = parse_projection_object(reply)
    if parsed is None:
        return None
    title = str(parsed.get("title") or "").strip()
    if not title or title in PLACEHOLDER_TITLES:
        return None
    return EventProjection(
        title=title[:20],
        event_type=str(parsed.get("event_type") or "家庭记录").strip()[:20],
        activity=str(parsed.get("activity") or "家庭活动").strip()[:20],
        summary=str(parsed.get("summary") or "").strip()[:240],
        confidence=_confidence(parsed.get("confidence")),
        model="event_summary",
    )


def _confidence(raw: object) -> float:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return MODEL_CONFIDENCE
    return min(1.0, max(0.0, value))


def parse_projection_object(text: str) -> dict[str, object] | None:
    stripped = text.strip()
    candidates = [stripped]
    start, end = stripped.find("{"), stripped.rfind("}")
    if 0 <= start < end:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def build_correction_system_prompt() -> str:
    """系统提示词。**它必须是 ``messages[0]``。**

    ``OpenAIChatModel.complete`` 的第一条硬契约就是这个（``chat_client.py:98``），
    1.0 的 ``400 System message must be at the beginning`` 是同一个坑的另一面。
    这段在 1.0 里是拼进**同一个 user 消息**发给 Ollama 的（``model_clients.py:314``
    的 ``chat(prompt)`` 只发一条 user 消息，宽松模板容忍）；2.0 的端口不接受那种形状，
    照搬 1.0 的单条 user 消息会让**每一次模型调用都在本地就抛**
    （``ChatModelError``），于是模型那条路永远走不到、只剩确定性摘要。
    """
    return (
        _SUMMARY_PROMPT
        + """
每条观察的 confirmed_people 给出身份提及绑定的当前姓名。
identity_updates 给出同一实体曾用的名称和关联 observation_ids。
重建摘要时以当前姓名为准，不沿用旧摘要或已被用户修正的身份。
有明确身份绑定时，用当前姓名称呼该人物，不继续用未命名的泛称代替。
name_state=confirmed 是用户命名；status=pending 时可称为用户标记的该姓名，但不能声称人脸身份已确认。
一条观察里可能有多个人：不得仅因出现一个已命名人物，就把所有“小男孩”等泛称都当作他。
只在证据能区分人物时将动作归给具体姓名；无法区分时保留其他人物的描述，明确共同出现即可。
只根据当前 observations 重建；已经删除的观察不能作为摘要依据。
文本记录须根据 text_content 重写摘要；正文与旧 caption 冲突时以正文为准，不延续旧摘要。
"""
    )


def build_correction_evidence(
    event: EventRecord,
    observations: Sequence[Observation],
    participants: Sequence[EventParticipantRecord] = (),
    identity_updates: Sequence[Mapping[str, object]] = (),
) -> str:
    """user 消息：这次要总结的事件的可验证证据（JSON，不含任何提示词）。

    ``participants`` 是**已确认身份**的人（``event_participants`` 由身份确认动作写入）。
    把它们显式列进证据，模型才可能写出「王建国和张晓莉在正定古城合影」；不列的话，
    它只能退化成「一名男子与一名女子」——系统明明知道是谁。
    """
    evidence = [
        {
            "observation_id": item.id,
            "caption": item.caption,
            "activity": item.activity,
            "place": item.place,
            "objects": list(item.objects),
            "clothing": list(item.clothing),
            "ocr_text": item.ocr_text,
            "source_type": item.source_type,
            "text_content": item.text_content,
            "confirmed_people": [
                {"entity_id": person.person_id, "name": person.person_name}
                for person in participants
                if item.id in person.evidence_ids and person.person_status == "confirmed"
            ],
        }
        for item in observations
    ]
    observation_ids = {item.id for item in observations}
    updates = [
        {**item, "observation_ids": [ref for ref in refs if ref in observation_ids]}
        for item in identity_updates
        if isinstance((refs := item.get("observation_ids")), (list, tuple))
        and observation_ids.intersection(refs)
    ]
    return json.dumps(
        {
            "time_start": event.time_start.isoformat() if event.time_start else None,
            "time_end": event.time_end.isoformat() if event.time_end else None,
            "place": event.place,
            "participants": [
                {
                    "entity_id": item.person_id,
                    "name": item.person_name,
                    "role": item.role,
                    "evidence_ids": list(item.evidence_ids),
                }
                for item in participants
                if item.person_name
            ],
            "identity_updates": updates,
            "observations": evidence,
            "speech_transcript": str(event.source_metadata.get("speech_transcript") or ""),
        },
        ensure_ascii=False,
    )


def build_system_prompt() -> str:
    """系统提示词。**它必须是 ``messages[0]``。**

    ``OpenAIChatModel.complete`` 的第一条硬契约就是这个（``chat_client.py:98``），
    1.0 的 ``400 System message must be at the beginning`` 是同一个坑的另一面。
    这段在 1.0 里是拼进**同一个 user 消息**发给 Ollama 的（``model_clients.py:314``
    的 ``chat(prompt)`` 只发一条 user 消息，宽松模板容忍）；2.0 的端口不接受那种形状，
    照搬 1.0 的单条 user 消息会让**每一次模型调用都在本地就抛**
    （``ChatModelError``），于是模型那条路永远走不到、只剩确定性摘要。
    """
    return _SUMMARY_PROMPT


def build_evidence(
    event: EventRecord,
    observations: Sequence[Observation],
    participants: Sequence[EventParticipantRecord] = (),
) -> str:
    """user 消息：这次要总结的事件的可验证证据（JSON，不含任何提示词）。

    ``participants`` 是**已确认身份**的人（``event_participants`` 由身份确认动作写入）。
    把它们显式列进证据，模型才可能写出「王建国和张晓莉在正定古城合影」；不列的话，
    它只能退化成「一名男子与一名女子」——系统明明知道是谁。
    """
    evidence = [
        {
            "observation_id": item.id,
            "caption": item.caption,
            "activity": item.activity,
            "place": item.place,
            "objects": list(item.objects),
            "clothing": list(item.clothing),
            "ocr_text": item.ocr_text,
        }
        for item in observations
    ]
    return json.dumps(
        {
            "time_start": event.time_start.isoformat() if event.time_start else None,
            "time_end": event.time_end.isoformat() if event.time_end else None,
            "place": event.place,
            "participants": [
                {"name": item.person_name, "role": item.role} for item in participants if item.person_name
            ],
            "observations": evidence,
            "speech_transcript": str(event.source_metadata.get("speech_transcript") or ""),
        },
        ensure_ascii=False,
    )


class SummarizeEvent:
    def __init__(
        self,
        *,
        events: EventRepository,
        observations: ObservationRepository,
        assets: AssetRepository,
        participants: EventParticipantRepository | None = None,
        model: ChatModel | None = None,
    ) -> None:
        self._events = events
        self._observations = observations
        self._assets = assets
        # 可空是为了让只读的测试与旧装配不被迫造一个参与者仓储；没有它时摘要退回
        # 「一名男子」那种描述，与这次改动之前的行为一致。
        self._participants = participants
        self._model = model

    def execute(
        self,
        event_id: str,
        *,
        correction: bool = False,
        identity_updates: Sequence[Mapping[str, object]] = (),
    ) -> EventRecord:
        """总结一个事件并落库。事件不存在时抛 ``KeyError``。

        返回的是**落库之后**的那一行：调用方拿到的是 title/summary 已更新的记录，
        而不是「我提交了什么」。
        """
        event = self._events.get(event_id)
        if event is None:
            raise KeyError(event_id)

        observations = [
            item
            for item in (
                self._observations.get(observation_id)
                for observation_id in self._events.observation_ids(event_id)
            )
            if item is not None
        ]
        if not correction:
            if not observations or self._model is None:
                return event
            people = self._participants.list_participants(event_id) if self._participants is not None else ()
            projection = self._from_model(event, observations, people) or fallback_projection(
                event, observations, people
            )
            return self._events.apply_summary(
                event_id,
                title=projection.title,
                event_type=projection.event_type,
                activity=projection.activity,
                summary=projection.summary,
            )

        usable = [item for item in observations if item.has_content]
        if observations and not usable:
            log.warning("事件无可用观察语义，保留待处理状态 event=%s", event_id)
            if correction:
                raise RuntimeError("事件尚无可用观察内容，请先完成媒体处理")
            return event
        if not observations or self._model is None:
            if correction:
                raise RuntimeError("事件摘要重建缺少观察或模型，请恢复后重试")
            return event

        known_places = {
            item.place.strip()
            for item in usable
            if item.place.strip() and item.place.strip() not in NOISE_VALUES | {OTHER_PLACE}
        }
        place = event.place
        if (not place or place in NOISE_VALUES | {OTHER_PLACE}) and len(known_places) == 1:
            place = next(iter(known_places))
            event = replace(event, place=place)

        people = self._participants.list_participants(event_id) if self._participants is not None else ()
        observation_ids = {item.id for item in observations}
        identity_updates = tuple(
            item
            for item in identity_updates
            if isinstance((refs := item.get("observation_ids")), (list, tuple))
            and observation_ids.intersection(refs)
        )
        projection = self._from_model(
            event, observations, people, correction=correction, identity_updates=identity_updates
        ) or fallback_projection(event, observations, people)
        return self._events.apply_summary(
            event_id,
            title=projection.title,
            event_type=projection.event_type,
            activity=projection.activity,
            summary=projection.summary,
            place=place,
        )

    def _from_model(
        self,
        event: EventRecord,
        observations: Sequence[Observation],
        participants: Sequence[EventParticipantRecord] = (),
        *,
        correction: bool = False,
        identity_updates: Sequence[Mapping[str, object]] = (),
    ) -> EventProjection | None:
        model = self._model
        if model is None:
            return None
        if correction:
            return self._from_correction_model(event, observations, participants, identity_updates)
        try:
            reply: ModelReply = model.complete(
                (
                    ChatMessage(role=ChatRole.SYSTEM, content=build_system_prompt()),
                    ChatMessage(
                        role=ChatRole.USER,
                        content=build_evidence(event, observations, participants),
                    ),
                ),
                (),
            )
        except Exception as error:  # 端点不可用/超时/返回不可解析，一律退回确定性摘要
            log.warning("事件总结的模型调用失败，改用确定性摘要: %s", error)
            return None
        projection = projection_from_reply(reply.content, event=event)
        if projection is None:
            log.info("事件总结的模型回复不可用，改用确定性摘要: %s", event.id)
        return projection

    def _from_correction_model(
        self,
        event: EventRecord,
        observations: Sequence[Observation],
        participants: Sequence[EventParticipantRecord],
        identity_updates: Sequence[Mapping[str, object]],
    ) -> EventProjection:
        """Validate identity consistency before publishing the whole automatic projection."""
        assert self._model is not None
        observation_ids = {item.id for item in observations}
        names = list(
            dict.fromkeys(
                [
                    person.person_name
                    for person in participants
                    if person.person_name
                    and person.person_status == "confirmed"
                    and observation_ids.intersection(person.evidence_ids)
                ]
                + [str(item["name"]) for item in identity_updates if item.get("name")]
            )
        )
        previous = {
            str(item["previous_name"])
            for item in identity_updates
            if item.get("previous_name")
            and item["previous_name"] != item.get("name")
            and not any(str(item["previous_name"]) in name for name in names)
        }
        messages: tuple[ChatMessage, ...] = (
            ChatMessage(
                role=ChatRole.SYSTEM,
                content=build_correction_system_prompt()
                + "\n本次是记忆修正：标题、摘要和活动须一致使用当前身份。"
                "有绑定姓名时，标题至少包含一个当前姓名，摘要包含绑定的当前姓名。"
                "不能仅给旧摘要加姓名冒号前缀；不要提及曾用名或改名过程。"
                "多人场景保留未命名人物，不能把他们的动作归给已命名人物。",
            ),
            ChatMessage(
                role=ChatRole.USER,
                content=build_correction_evidence(event, observations, participants, identity_updates),
            ),
        )
        for attempt in range(2):
            try:
                reply = self._model.complete(messages, ())
            except Exception as error:
                raise RuntimeError("事件摘要重建模型调用失败") from error
            projection = projection_from_reply(reply.content, event=event)
            if projection is None or not projection.summary.strip():
                projection = fallback_projection(event, observations, participants)
            summary_body = projection.summary
            prefix, separator, body = summary_body.partition("：")
            if separator and prefix and all(name in names for name in prefix.split("、")):
                summary_body = body
            missing = bool(names) and (
                not any(name in projection.title for name in names)
                or not all(name in summary_body for name in names)
            )
            stale = any(
                old in value
                for old in previous
                for value in (projection.title, projection.summary, projection.activity)
            )
            if not missing and not stale:
                return projection
            if attempt == 0:
                messages = (
                    *messages,
                    ChatMessage(role=ChatRole.ASSISTANT, content=reply.content),
                    ChatMessage(
                        role=ChatRole.USER,
                        content="标题或摘要没有同步当前姓名，或还包含旧姓名。请依据同一份证据重新生成完整JSON，"
                        "标题至少含一个当前姓名，摘要使用所有绑定姓名；不输出曾用名。",
                    ),
                )
        raise RuntimeError("事件标题与摘要未满足当前身份一致性约束，请重试")
