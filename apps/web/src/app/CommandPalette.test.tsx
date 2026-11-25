import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NAV_ITEMS, SYSTEM_ITEMS } from "./nav";
import { CommandPalette, resolveCommand } from "./CommandPalette";

const ITEMS = [...NAV_ITEMS, ...SYSTEM_ITEMS];

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

function submit(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
  fireEvent.submit(input.closest("form") as HTMLFormElement);
}

afterEach(cleanup);

describe("resolveCommand", () => {
  it("命中文案里的页面名", () => {
    expect(resolveCommand("时间线", ITEMS)).toEqual({
      kind: "nav",
      item: NAV_ITEMS.find((item) => item.id === "timeline"),
    });
  });

  it("命中导航 id（1.0 的 includes(item.id) 语义）", () => {
    const result = resolveCommand("open ASSISTANT", ITEMS);
    expect(result.kind).toBe("nav");
    expect(result.kind === "nav" && result.item.path).toBe("/assistant");
  });

  it("包含页面名的长句也当跳转", () => {
    const result = resolveCommand("打开资料库看看", ITEMS);
    expect(result.kind).toBe("nav");
    expect(result.kind === "nav" && result.item.path).toBe("/library");
  });

  it("没命中页面时当作问题", () => {
    expect(resolveCommand("去年在海边拍的照片", ITEMS)).toEqual({
      kind: "question",
      query: "去年在海边拍的照片",
    });
  });

  it("空输入不动作", () => {
    expect(resolveCommand("   ", ITEMS)).toEqual({ kind: "empty" });
  });
});

describe("CommandPalette", () => {
  it("输入页面名后跳转并关闭", () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <CommandPalette onClose={onClose} />
        <LocationProbe />
      </MemoryRouter>,
    );

    submit(screen.getByLabelText("命令"), "资料库");
    expect(screen.getByTestId("location").textContent).toBe("/library");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("输入问题后交给助手页", () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <CommandPalette onClose={onClose} />
        <LocationProbe />
      </MemoryRouter>,
    );

    submit(screen.getByLabelText("命令"), "冰箱");
    expect(screen.getByTestId("location").textContent).toBe(
      `/assistant?q=${encodeURIComponent("冰箱")}`,
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("页面按钮直接跳转（1.0 的 command-links）", () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <CommandPalette onClose={onClose} />
        <LocationProbe />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "添加资料" }));
    expect(screen.getByTestId("location").textContent).toBe("/imports");
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
