"""按 conversation 固定 llama.cpp 槽位。

Qwen3.5 的混合线性注意力（GatedDeltaNet）在槽位里保留的是循环状态，只有整轮对话
都落在同一个 ``id_slot`` 上，下一轮才能复用上一轮的前缀。共享实例上让服务端按
LCP 自选槽位，其他流量一占位就会全量重算；而全局 pin 又会被别的对话挤掉。

这里用「租约」把**当前在跑的** conversation 均匀分到不同槽位：取回自己记得的
槽位（不再用哈希，哈希分布不均会把多轮请求排到同一槽位后面），被占用时改用空闲
槽位；轮结束即释放租约，但保留亲和映射，下一轮仍回到原槽位。单槽（Mac）直接
不 pin，交给 llama.cpp 的默认槽位。
"""

from __future__ import annotations

from collections import OrderedDict
from contextvars import ContextVar
from threading import Lock

_request_slot: ContextVar[int | None] = ContextVar("sentrix_request_slot", default=None)

_MAX_AFFINITY = 1024

_lock = Lock()
_preferred: OrderedDict[str, int] = OrderedDict()
_busy: set[int] = set()
_round_robin = 0


def request_slot() -> int | None:
    """当前请求上下文里要发送给 llama.cpp 的 ``id_slot``；``None`` 表示不 pin。"""
    return _request_slot.get()


def acquire_slot(conversation_id: str, slots: int) -> None:
    """为一个 conversation 租一个槽位，并在当前上下文里固定它。"""
    global _round_robin
    slots = max(1, int(slots))
    if slots <= 1:
        _request_slot.set(None)
        return

    with _lock:
        slot = _preferred.get(conversation_id)
        if slot is None or slot >= slots or slot in _busy:
            slot = next((index for index in range(slots) if index not in _busy), None)
        if slot is None:
            # 槽位全忙（应有执行器数量兜底），只能排队；轮转保证至少不再集中到一处。
            slot = _round_robin % slots
            _round_robin += 1
        _preferred[conversation_id] = slot
        _preferred.move_to_end(conversation_id)
        while len(_preferred) > _MAX_AFFINITY:
            _preferred.popitem(last=False)
        _busy.add(slot)

    _request_slot.set(slot)


def release_slot(conversation_id: str) -> None:
    """释放租约；亲和映射保留，下一轮同一 conversation 仍回到这个槽位。"""
    with _lock:
        slot = _preferred.get(conversation_id)
        if slot is not None:
            _busy.discard(slot)
    _request_slot.set(None)
