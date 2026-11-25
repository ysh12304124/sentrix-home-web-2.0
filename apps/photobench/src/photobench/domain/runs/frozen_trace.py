"""Immutable completed execution traces shared across checkpoint revisions.

Judging changes answer metadata, never the recorded execution. Keep these JSON
containers compatible with ordinary readers while making that boundary explicit.
"""

from __future__ import annotations

from typing import Any, NoReturn


def _readonly(*args: Any, **kwargs: Any) -> NoReturn:
    raise TypeError("completed execution traces are immutable")


class FrozenTraceDict(dict[str, Any]):
    __slots__ = ()
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _readonly

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenTraceDict:
        return self

    def __reduce__(self) -> tuple[Any, ...]:
        return FrozenTraceDict, (dict(self),)


class FrozenTraceList(list[Any]):
    __slots__ = ()
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = _readonly
    reverse = sort = __iadd__ = __imul__ = _readonly

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenTraceList:
        return self

    def __reduce__(self) -> tuple[Any, ...]:
        return FrozenTraceList, (list(self),)


TRACE_TYPES = (FrozenTraceDict, FrozenTraceList)
ITEM_TRACE_FIELDS = ("tool_trace", "execution_trace", "model_call_metrics")
TURN_TRACE_FIELDS = ("trace", "debug_trace", "trace_bundle", "agent2_trace")


def freeze_trace(value: Any, memo: dict[int, Any]) -> Any:
    if isinstance(value, TRACE_TYPES) or not isinstance(value, (dict, list)):
        return value
    if id(value) in memo:
        return memo[id(value)]
    if isinstance(value, dict):
        result: Any = FrozenTraceDict()
        memo[id(value)] = result
        dict.update(result, ((key, freeze_trace(child, memo)) for key, child in value.items()))
    else:
        result = FrozenTraceList()
        memo[id(value)] = result
        list.extend(result, (freeze_trace(child, memo) for child in value))
    return result


def freeze_item_traces(items: list[dict[str, Any]]) -> None:
    """Called under the state lock after an answer has entered the result list.

    Score fields and turn metadata remain mutable. Replacing a trace after a
    retest creates a new immutable value on the next checkpoint.
    """
    memo: dict[int, Any] = {}
    for item in items:
        for key in ITEM_TRACE_FIELDS:
            if key in item:
                item[key] = freeze_trace(item[key], memo)
        for turn in item.get("runtime_turns") or []:
            for key in TURN_TRACE_FIELDS:
                if key in turn:
                    turn[key] = freeze_trace(turn[key], memo)
