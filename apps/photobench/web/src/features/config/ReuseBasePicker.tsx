/**
 * 复用基座下拉。逐行移植 1.0 `App.vue:2884-2889` 的第三个 `<label>`（`v-if="runMode === 'reuse'"`）。
 *
 * 可见性由调用方（`ConfigSection`）按 `runMode` 决定，这里只渲染这一个 `<label>` 并
 * 由外层 `.config-grid.config-grid-scope` 当网格子项摆放——和 1.0 的 DOM 结构一致。
 *
 * 状态来自 `useReuseMode`（`reuseBases` / `selectedReuseBaseId` / `scopeAlbumHint`），
 * 组件本身不发请求；`select` 的写回走 `setSelectedReuseBaseId`，1.0 的两个 `watch`
 * 联动（联动推导 QA 数据集 / 相册）在那个 hook 里。
 */
import type { ReuseBase } from "../../api/types.ts";

export interface ReuseBasePickerProps {
  reuseBases: ReuseBase[];
  selectedReuseBaseId: string;
  onSelectReuseBase: (baseId: string) => void;
  memorySpacesLoading: boolean;
  /** `useReuseMode.scopeAlbumHint`。 */
  scopeAlbumHint: string;
}

export function ReuseBasePicker({
  reuseBases,
  selectedReuseBaseId,
  onSelectReuseBase,
  memorySpacesLoading,
  scopeAlbumHint,
}: ReuseBasePickerProps) {
  return (
    <label>
      复用基座
      <select
        value={selectedReuseBaseId}
        disabled={memorySpacesLoading}
        onChange={(event) => onSelectReuseBase(event.target.value)}
      >
        {/* 1.0 是 `v-model` + 一个 `value="" disabled` 的占位 option。 */}
        <option value="" disabled>
          {memorySpacesLoading
            ? "加载中…"
            : reuseBases.length
              ? "请选择相册基座（相册 + 模型）"
              : "无可复用基座（检查 Sentrix 后端地址）"}
        </option>
        {reuseBases.map((base) => (
          <option key={base.base_id} value={base.base_id} disabled={!base.ready}>
            {base.album_id} · {base.model_profile} · {base.scope_name}{!base.ready ? "（构建未确认完成）" : ""}
          </option>
        ))}
      </select>
      <span className="config-help">
        {scopeAlbumHint || "选择后直接复用已生成相册，不重新处理照片"}
      </span>
    </label>
  );
}
