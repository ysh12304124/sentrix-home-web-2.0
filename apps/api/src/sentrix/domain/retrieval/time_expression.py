"""把「2023 年 10 月」这类时间表达解析成区间。纯规则，零 IO。

逐行移植 1.0 ``backend/query_contracts.py:310`` 的 ``parse_time_expression``。

**为什么时间条件非要有它不可。** 时间条件是**硬**的（``conditions.evaluate`` 按
``strictness == HARD`` 决定要不要排除候选），而用户与模型给出的从来不是区间，
是「去年」「2023 年 10 月」这样的说法。少了这一步，「时间硬条件」就只能接受
已经算好的区间——也就是说，**真正的入口没人接**，而接口看上去是通的。

**四档优先级照抄 1.0**，顺序本身是语义：

1. 月范围（``2025年3月-2025年5月`` / ``2025年3月-5月``）
2. 年范围（``2025年-2026年``）
3. 月/日（``2023年10月`` / ``2023-10`` / ``2023年10月5日``）
4. 纯年份（``2023`` / ``2023年``）

**顺序错了会静默出错。** 把「纯年份」提到前面，``2025年3月-2025年5月`` 会被
第 4 档的 ``fullmatch`` 判失败之后落到第 3 档，解析成「2025 年 3 月」一天——
**返回了一个区间、看起来完全正常，只是短了两个月**。

**解析不出来返回 ``None``，不猜。** 调用方拿 ``None`` 就知道这条时间条件
没被理解——那与「解析成今天」是完全不同的两件事。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 范围两端的连接词。1.0 的字面量。
_RANGE_SEPARATORS = "[-~至到]"

_MONTH_RANGE = re.compile(
    rf"((?:19|20)\d{{2}})\s*年?\s*(\d{{1,2}})\s*月?\s*{_RANGE_SEPARATORS}\s*"
    rf"((?:19|20)\d{{2}})?\s*年?\s*(\d{{1,2}})\s*月?"
)
_YEAR_RANGE = re.compile(rf"((?:19|20)\d{{2}})\s*年?\s*{_RANGE_SEPARATORS}\s*((?:19|20)\d{{2}})\s*年?")
_MONTH_DAY = re.compile(r"((?:19|20)\d{2})\s*(?:年|[-/.])\s*(\d{1,2})\s*(?:月|[-/.])?(?:(\d{1,2})\s*日?)?")
_PURE_YEAR = re.compile(r"((?:19|20)\d{2})\s*年?")


def parse_time_expression(value: str | None) -> tuple[datetime, datetime] | None:
    """时间表达 → 左闭右开区间；认不出来返回 ``None``。"""
    cleaned = str(value or "").strip()
    # 显式查询时区写作 2018-04-01[Asia/Shanghai]；省略时按媒体当地日历。
    zone_suffix = re.fullmatch(r"(.*?)\[([A-Za-z_]+(?:/[A-Za-z_+\-]+)+|UTC)\]", cleaned)
    if zone_suffix:
        bounds = parse_time_expression(zone_suffix.group(1))
        try:
            zone = ZoneInfo(zone_suffix.group(2))
        except ZoneInfoNotFoundError:
            return None
        return (bounds[0].replace(tzinfo=zone), bounds[1].replace(tzinfo=zone)) if bounds else None

    month_range = _MONTH_RANGE.fullmatch(cleaned)
    if month_range:
        start_year, start_month = int(month_range.group(1)), int(month_range.group(2))
        end_year = int(month_range.group(3)) if month_range.group(3) else start_year
        end_month = int(month_range.group(4))
        start = datetime(start_year, start_month, 1)
        # 右端取**下个月一号**，所以「3 月到 5 月」包含整个 5 月。
        # 12 月要进位到次年 1 月——漏掉这一步会让「到 12 月」变成空区间。
        end = datetime(end_year + (end_month == 12), 1 if end_month == 12 else end_month + 1, 1)
        return start, end

    year_range = _YEAR_RANGE.fullmatch(cleaned)
    if year_range:
        return datetime(int(year_range.group(1)), 1, 1), datetime(int(year_range.group(2)) + 1, 1, 1)

    month_day = _MONTH_DAY.search(cleaned)
    if month_day:
        year, month = int(month_day.group(1)), int(month_day.group(2))
        day = month_day.group(3)
        if day:
            start = datetime(year, month, int(day))
            return start, start + timedelta(days=1)
        start = datetime(year, month, 1)
        return start, datetime(year + (month == 12), 1 if month == 12 else month + 1, 1)

    pure_year = _PURE_YEAR.fullmatch(cleaned)
    if pure_year:
        year = int(pure_year.group(1))
        return datetime(year, 1, 1), datetime(year + 1, 1, 1)

    return None
