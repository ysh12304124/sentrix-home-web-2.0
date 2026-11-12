import type { KeyboardEvent } from "react";

/**
 * 单行编辑框的快捷键：`Enter` 保存、`Esc` 取消。
 *
 * **输入法组字时两个键都交给输入法**：用拼音选字按的回车、关掉候选框按的 Esc 都不是
 * 用户在提交或放弃。`isComposing` 之外再认 `keyCode === 229`，是因为 Safari 在
 * compositionend 之后还会补发一次回车的 keydown，只有这个码能把它认出来。
 *
 * Esc 不再向上冒泡：外层详情侧栏若也认 Esc，不能一键把整个侧栏关掉。
 */
export function editKeyHandler(actions: { onSubmit: () => void; onCancel: () => void }) {
  return (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      actions.onCancel();
    } else if (event.key === "Enter") {
      event.preventDefault();
      actions.onSubmit();
    }
  };
}
