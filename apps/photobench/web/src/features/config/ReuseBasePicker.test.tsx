import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReuseBase } from "../../api/types.ts";
import { ReuseBasePicker } from "./ReuseBasePicker.tsx";

const BASE: ReuseBase = {
  run_id: "r1",
  base_id: "b1",
  album_id: "albumX",
  model_profile: "prof",
  scope_id: "s1",
  scope_name: "scope",
  created_at: "",
  source_run_ids: [],
  ready: true,
};

afterEach(cleanup);

describe("ReuseBasePicker", () => {
  it("加载中：占位文案是「加载中…」且下拉禁用", () => {
    render(
      <ReuseBasePicker
        reuseBases={[]}
        selectedReuseBaseId=""
        onSelectReuseBase={vi.fn()}
        memorySpacesLoading
        scopeAlbumHint=""
      />,
    );
    const select = screen.getByRole("combobox", { name: /复用基座/ }) as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    expect(screen.getByRole("option", { name: "加载中…" })).toBeTruthy();
  });

  it("无基座时提示检查 Sentrix 后端地址", () => {
    render(
      <ReuseBasePicker
        reuseBases={[]}
        selectedReuseBaseId=""
        onSelectReuseBase={vi.fn()}
        memorySpacesLoading={false}
        scopeAlbumHint=""
      />,
    );
    expect(screen.getByRole("option", { name: "无可复用基座（检查 Sentrix 后端地址）" })).toBeTruthy();
    expect(screen.getByText("选择后直接复用已生成相册，不重新处理照片")).toBeTruthy();
  });

  it("有基座：选项是「相册 · 模型 · scope」，选择后回调 base_id；hint 覆盖默认文案", () => {
    const onSelect = vi.fn();
    render(
      <ReuseBasePicker
        reuseBases={[BASE]}
        selectedReuseBaseId=""
        onSelectReuseBase={onSelect}
        memorySpacesLoading={false}
        scopeAlbumHint="复用基座：albumX · prof；QA 数据集：albumX"
      />,
    );
    const select = screen.getByRole("combobox", { name: /复用基座/ }) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "b1" } });
    expect(onSelect).toHaveBeenCalledWith("b1");
    expect(screen.getByRole("option", { name: "albumX · prof · scope" })).toBeTruthy();
    expect(screen.getByText("复用基座：albumX · prof；QA 数据集：albumX")).toBeTruthy();
  });
});
