"""「拿一个问题去问这张图」的提示词与它的槽位（T123/T124 共用）。

1.0 的 ``inspect_photo`` 与 ``read_photo_text`` 是两个工具，但它们对模型的那一问是
同一件事：一张图 + 一个问题 → 一次结构化回答。1.0 把这件事分开写在
``agent_runtime/tools.py:2122``（``_inspect_photo``）与
``agent_runtime/ocr_tool.py:495``（``_read_photo_text``）里，各自拼自己的提示词、
各自调 ``gamma.chat``。

2.0 把它提成一个端口（``domain/media/ports.py`` 的 ``PhotoInspector``）与本模块的
提示词槽位，理由与 ``PersonVision`` 相同：同一件事写两遍，最容易写错的两处
——**图片编码、超时与「模型不可用」的降级**——就会有两份实现，各自测各自的。

**这个文件为什么在 domain。** 提示词是产品行为，不是实现细节——``domain/identity/moments.py``
的 ``PERSON_MOMENT_PROMPT`` 与 ``domain/identity/portrait.py`` 的 ``PERSON_PORTRAIT_PROMPT``
是同一条理由。住在这里之后，用例能对着 1.0 的原文逐字节断言，改一个字就红。

**``certainty`` 只有两档，没有第三档。** 提示词自己写死 ``supported|uncertain``：
模型答不上来时报 ``uncertain``，**不是** ``contradicted``——这与
``domain/retrieval/conditions.py`` 里 UNKNOWN 与 CONTRADICTED 分开是同一个道理。
移植时不要顺手加「确定没有」那一档。

**这一个模块只装提示词，不装解析。** 两条工具各自决定往槽位里填什么、以及怎么从回来的
那包 JSON 里取自己要的字段（§4 Task 123/124）。所以这里没有 ``certainty`` 的兜底、
没有 ``observation`` 的取值——那是工具层的事。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

# 1.0 ``agent_runtime/tools.py:2616`` 的 ``_INSPECT_PROMPT`` 原文，**逐字节相同**。
# 双花括号是 ``str.format`` 的转义（1.0 也是这么写的），所以照抄。
INSPECT_PROMPT = """观察这张照片，输出 JSON：
{{"observation": "一句话描述", "certainty": "supported|uncertain", "target_face_id": "face_N或空"}}
{target_instruction}
问题：{question}
如果问题指向照片中的某个人但没有姓名，请根据人脸清单选择对应的 face_id；无法确定时返回空，不要猜姓名。"""

EVIDENCE_INSTRUCTION = (
    "\n依据要求：只报告画面直接支持的内容。计数时按空间位置逐个列出可辨对象后给出总数，"
    "明确区分完整可见、遮挡和画面外对象；无法精确计数时 certainty=uncertain。"
    "同一对象跨画面出现只计一次。地点文字按原文报告，城市或地址需明确标识支持。"
)

# 1.0 ``:2619-2620`` 的 ``target_instruction`` 原文。``{target_person}`` 是它唯一的槽。
TARGET_INSTRUCTION = (
    "目标人物是“{target_person}”。第一张图（如有）是该人物的定位裁剪图；"
    "只回答目标人物，不要把同图其他人的外观或动作归给目标人物。"
)

# 1.0 ``:2205``：人脸清单**拼在提示词之后**，不是提示词里的一个槽（1.0 也是先
# ``.format()`` 再 ``+ face_context``）。
FACE_MANIFEST_PREFIX = "\n照片中的人脸清单（face_id 与位置一一对应；未确认者不得猜姓名）："

# 1.0 ``:2255`` 的 ``parsed.get("certainty") or "supported"``——**默认值是 supported**。
# 提示词里那两个取值就是全部：见模块 docstring，不要加第三档。
CERTAINTY_SUPPORTED = "supported"
CERTAINTY_UNCERTAIN = "uncertain"


def render_face_manifest(faces: Sequence[Mapping[str, object]]) -> str:
    """人脸清单那一段提示词。**没有人脸时返回空串**（1.0 ``:2204`` 的 ``if face_manifest:``）。

    原样 ``json.dumps`` 给过来的每一行，**一个字段都不补**。「未确认的人不许有名字」这条
    纪律的落点是**造这张表的那一层**（1.0 ``_photo_face_manifest`` 只在 cluster 与 entity
    双确认时才填 ``person_name``，``:2343-2355``）。在这里顺手替某一行填一个名字，等于把
    那条纪律从「查过两次状态」降级成「猜的」——而那正是它要防的事。
    """
    if not faces:
        return ""
    return FACE_MANIFEST_PREFIX + json.dumps(list(faces), ensure_ascii=False)


def build_inspect_prompt(
    question: str,
    *,
    target_person: str = "",
    faces: Sequence[Mapping[str, object]] = (),
) -> str:
    """填两个槽、再把人脸清单拼在后面——顺序与 1.0 ``tools.py:2209-2213`` 一致。

    ``target_instruction`` 的判据是「**有没有指名目标人物**」，不是「裁剪图有没有做出来」：
    1.0 在裁剪失败（``target_status = "not_located"``）时**照样**填这句话，因为
    ``TARGET_INSTRUCTION`` 自己写着「（如有）」。

    ``question`` 由调用方给。1.0 的工具层在参数缺席时用的是 ``"请描述这张照片"``
    （``tools.py:2125`` 的 ``arguments.get("question") or ...``）——那是**工具的入参默认值**，
    属于 T123，不住在这里。
    """
    instruction = TARGET_INSTRUCTION.format(target_person=target_person) if target_person else ""
    return (
        INSPECT_PROMPT.format(question=question, target_instruction=instruction)
        + EVIDENCE_INSTRUCTION
        + render_face_manifest(faces)
    )
