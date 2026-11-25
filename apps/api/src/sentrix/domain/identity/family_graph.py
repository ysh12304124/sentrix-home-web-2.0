"""家庭关系的正反向标签白名单（S7b1 · T302）。

对应 1.0 ``backend/family_graph.py``，**逐字搬**——那个文件总共 43 行，只有这张表和
一个校验函数，没有 import。

**它是 1.0 里唯一受约束的关系词表。** 另一条关系线（通用 ``relationships`` 表，
S7a 落的）的 ``predicate`` 是**自由文本**，任何串都能写进去（实测出现过「出现在」
「共同出现」「妻子」）。这一张表只服务家庭图谱：

- 边**不能**离开这张表——``set_family_relationship`` 在落库前必须过
  ``validate_relationship_pair``；
- 表是**一对多**的：``父亲`` 的合法反向是 ``{"儿子", "女儿"}``——**孩子的性别决定
  用哪个**，所以一条 ``父亲 → X`` 的边必须同时给出 ``X`` 该用的那个词；
- ``朋友`` / ``密友`` / ``其他亲属`` 是**自反**的（逆还是自己）；
- ``哥哥/姐姐/弟弟/妹妹`` 四个的合法反向是**整个四元组**（兄妹之间的长幼与性别组合
  都能对上），这是这张表里唯一一处「一组对一组」。

**不要按 ``person_graph.py`` 的 ``INVERSE_RELATION`` 去「统一」它**——那是 1.0 另一个
文件里的另一张表，粒度更粗（含「同事/老师/照护者」，且「孩子」的逆是「父母」）。
两张表服务不同的东西，1.0 就没统一过。
"""

from __future__ import annotations

import unicodedata

RELATION_INVERSES: dict[str, set[str]] = {
    "丈夫": {"妻子"},
    "妻子": {"丈夫"},
    "父亲": {"儿子", "女儿"},
    "母亲": {"儿子", "女儿"},
    "儿子": {"父亲", "母亲"},
    "女儿": {"父亲", "母亲"},
    "哥哥": {"哥哥", "姐姐", "弟弟", "妹妹"},
    "姐姐": {"哥哥", "姐姐", "弟弟", "妹妹"},
    "弟弟": {"哥哥", "姐姐", "弟弟", "妹妹"},
    "妹妹": {"哥哥", "姐姐", "弟弟", "妹妹"},
    "祖父": {"孙子", "孙女"},
    "祖母": {"孙子", "孙女"},
    "外祖父": {"外孙", "外孙女"},
    "外祖母": {"外孙", "外孙女"},
    "孙子": {"祖父", "祖母"},
    "孙女": {"祖父", "祖母"},
    "外孙": {"外祖父", "外祖母"},
    "外孙女": {"外祖父", "外祖母"},
    "叔叔": {"侄子", "侄女"},
    "伯伯": {"侄子", "侄女"},
    "姑姑": {"侄子", "侄女"},
    "舅舅": {"外甥", "外甥女"},
    "姨妈": {"外甥", "外甥女"},
    "姨母": {"外甥", "外甥女"},
    "侄子": {"叔叔", "伯伯", "姑姑"},
    "侄女": {"叔叔", "伯伯", "姑姑"},
    "外甥": {"舅舅", "姨妈", "姨母"},
    "外甥女": {"舅舅", "姨妈", "姨母"},
    "朋友": {"朋友"},
    "密友": {"密友"},
    "其他亲属": {"其他亲属"},
}


# 用户自定义称谓的长度上限。图上的边标签与详情里的整句都要放得下。
MAX_CUSTOM_LABEL_LENGTH = 16


def validate_custom_relationship_pair(predicate: str, inverse_predicate: str) -> None:
    """用户自己写的一对称谓（2026-11-12 起建关系不再从词表里选）。

    只做形状校验：strip 后非空、不超过 ``MAX_CUSTOM_LABEL_LENGTH`` 个字、不含控制字符。
    **白名单只约束模型推断**（``validate_relationship_pair``）——用户写「同事」「干妈」
    「师父」都是他自己的判断，不该被一张亲属词表拒之门外。
    """
    for label in (predicate, inverse_predicate):
        clean = str(label or "").strip()
        if not clean:
            raise ValueError("关系标签不能为空")
        if len(clean) > MAX_CUSTOM_LABEL_LENGTH:
            raise ValueError(f"关系标签不能超过 {MAX_CUSTOM_LABEL_LENGTH} 个字: {clean}")
        if any(unicodedata.category(char).startswith("C") for char in clean):
            raise ValueError("关系标签不能包含控制字符")


def validate_relationship_pair(predicate: str, inverse_predicate: str) -> None:
    """校验一对正反向标签，不合法抛 ``ValueError``。1.0 ``family_graph.py:36-41``。

    **三条边界照搬，每一条的报错话术也是交付内容**（调用方按消息区分）：

    - 任一为空（含 ``None``）→ ``关系标签不能为空``；
    - 正向不在表里（或表里那一项为空集）→ ``关系标签不匹配: X -> Y``；
    - 反向不在正向的合法集合里 → 同一句话。

    后两条合并成一句，是因为 1.0 就是 ``if not allowed or inverse not in allowed``
    一个判断——分开写会多出一种 1.0 没有的消息。
    """
    predicate = str(predicate or "").strip()
    inverse_predicate = str(inverse_predicate or "").strip()
    if not predicate or not inverse_predicate:
        raise ValueError("关系标签不能为空")
    allowed = RELATION_INVERSES.get(predicate)
    if not allowed or inverse_predicate not in allowed:
        raise ValueError(f"关系标签不匹配: {predicate} -> {inverse_predicate}")
