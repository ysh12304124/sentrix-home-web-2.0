import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../shared/api/client")>();
  return {
    ...actual,
    setPersonHidden: vi.fn().mockResolvedValue(undefined),
    getPerson: vi.fn(),
    rejectFaceCluster: vi.fn().mockResolvedValue({}),
  };
});

import * as client from "../../../shared/api/client";
import type { FamilyPerson } from "../../../shared/api/client";
import { FamilyRoster } from "./FamilyRoster";

function person(overrides: Partial<FamilyPerson>): FamilyPerson {
  return {
    id: "p",
    scope_id: "album1",
    display_name: "未确认姓名人物",
    membership: null,
    portrait: null,
    avatar_face_instance_id: null,
    appearance_count: 1,
    representative_media: [],
    status: "pending",
    aliases: [],
    hidden: false,
    ...overrides,
  };
}

const PEOPLE = [
  person({ id: "mom", display_name: "妈妈", status: "confirmed", appearance_count: 1, aliases: ["阿娟"] }),
  person({ id: "dad", display_name: "爸爸", status: "confirmed", appearance_count: 12 }),
  person({ id: "often", appearance_count: 5 }),
  person({ id: "once_a", appearance_count: 1 }),
  person({ id: "once_b", appearance_count: 2 }),
  person({ id: "neighbour", display_name: "邻居", status: "confirmed", appearance_count: 4, hidden: true }),
];

function mount(people = PEOPLE, onChanged = vi.fn().mockResolvedValue(undefined)) {
  render(<FamilyRoster people={people} selectedId={null} onSelect={vi.fn()} onChanged={onChanged} />);
  return onChanged;
}

const region = (name: string) => screen.getByRole("region", { name });
const ids = (root: HTMLElement) =>
  [...root.querySelectorAll("[data-person-id]")].map((node) => node.getAttribute("data-person-id"));
const openInbox = () => fireEvent.click(screen.getByRole("tab", { name: /待认识/ }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("名录两个视图", () => {
  it("默认是「我的人物」：只有认出来的人；待认识的面孔不混在里面", () => {
    mount();
    expect(screen.getByRole("tab", { name: /我的人物/ }).getAttribute("aria-selected")).toBe("true");
    expect(ids(region("我的人物"))).toEqual(["dad", "mom"]);
    expect(screen.queryByRole("region", { name: "常出现的面孔" })).toBeNull();
    // 数字跟着视图走：我的人物 2，待认识 = 1 常出现 + 3 偶遇（含标为路人的邻居）。
    expect(screen.getByRole("tab", { name: /我的人物/ }).textContent).toContain("2");
    expect(screen.getByRole("tab", { name: /待认识/ }).textContent).toContain("4");
  });

  it("归属筛选只数认出来的人", () => {
    mount();
    const filters = screen.getByRole("group", { name: "按归属筛选" });
    expect(within(filters).getByRole("button", { name: /待归类/ }).textContent).toContain("2");
  });

  it("「待认识」里分常出现与偶遇两组，偶遇默认展开", () => {
    mount();
    openInbox();
    expect(ids(region("常出现的面孔"))).toEqual(["often"]);
    const tray = region("偶遇面孔");
    expect(ids(tray).sort()).toEqual(["neighbour", "once_a", "once_b"]);
    expect((tray.querySelector("details") as HTMLDetailsElement).open).toBe(true);
  });

  it("一个人都还没认出来时，默认停在「待认识」", () => {
    mount(PEOPLE.filter((item) => item.status === "pending"));
    expect(screen.getByRole("tab", { name: /待认识/ }).getAttribute("aria-selected")).toBe("true");
  });

  it("按别名搜索；另一个视图也有匹配时提示并能切过去", () => {
    mount();
    fireEvent.change(screen.getByLabelText("搜索人物"), { target: { value: "阿娟" } });
    expect(ids(region("我的人物"))).toEqual(["mom"]);

    fireEvent.change(screen.getByLabelText("搜索人物"), { target: { value: "邻居" } });
    expect(screen.getByText(/「待认识」里还有 1 个匹配/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "去看看" }));
    expect(ids(region("偶遇面孔"))).toEqual(["neighbour"]);
  });

  it("没有任何合并入口", () => {
    mount();
    expect(screen.queryByRole("button", { name: /合并/ })).toBeNull();
    openInbox();
    fireEvent.click(within(region("偶遇面孔")).getByRole("button", { name: "批量整理" }));
    expect(screen.queryByRole("button", { name: /合并/ })).toBeNull();
  });
});

describe("偶遇面孔的批量整理", () => {
  function choose(...personIds: string[]) {
    openInbox();
    const tray = region("偶遇面孔");
    fireEvent.click(within(tray).getByRole("button", { name: "批量整理" }));
    for (const id of personIds) fireEvent.click(tray.querySelector(`[data-person-id="${id}"]`) as Element);
    return tray;
  }

  it("标为路人逐个写 hidden 属性，再重拉一次", async () => {
    const onChanged = mount();
    const tray = choose("once_a", "once_b");
    fireEvent.click(within(tray).getByRole("button", { name: "标为路人" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(client.setPersonHidden).toHaveBeenCalledWith("once_a", true);
    expect(client.setPersonHidden).toHaveBeenCalledWith("once_b", true);
    expect(await within(tray).findByRole("status")).toHaveProperty("textContent", "已将 2 人标为路人");
  });

  it("「这不是人物」要二次确认，只拒绝待确认的人的有效人脸分组", async () => {
    vi.mocked(client.getPerson).mockImplementation(
      async (id: string) => ({ entity: { aliases: [] }, clusters: [{ id: `cluster_${id}`, status: "pending" }] }) as never,
    );
    const onChanged = mount();
    const tray = choose("once_a", "neighbour");
    fireEvent.click(within(tray).getByRole("button", { name: "这不是人物" }));
    expect(client.rejectFaceCluster).not.toHaveBeenCalled();
    fireEvent.click(within(tray).getByRole("button", { name: "确定移除" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    // 邻居已确认，后端会拒绝；界面只对待确认的 once_a 发请求。
    expect(client.rejectFaceCluster).toHaveBeenCalledTimes(1);
    expect(client.rejectFaceCluster).toHaveBeenCalledWith("cluster_once_a");
  });
});
