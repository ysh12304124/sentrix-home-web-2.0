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

function mount(onChanged = vi.fn().mockResolvedValue(undefined)) {
  render(
    <FamilyRoster
      people={PEOPLE}
      selectedId={null}
      onSelect={vi.fn()}
      onMerged={vi.fn()}
      onChanged={onChanged}
    />,
  );
  return onChanged;
}

const tier = (name: string) => screen.getByRole("region", { name });
const ids = (root: HTMLElement) =>
  [...root.querySelectorAll("[data-person-id]")].map((node) => node.getAttribute("data-person-id"));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("名录三层", () => {
  it("已确认的人（哪怕只出现一次）在最前；常见的待确认面孔单独一层；其余收进偶遇面孔", () => {
    mount();
    expect(ids(tier("我的人物"))).toEqual(["dad", "mom"]);
    expect(ids(tier("可能认识"))).toEqual(["often"]);
    const tray = tier("偶遇面孔");
    expect(ids(tray).sort()).toEqual(["neighbour", "once_a", "once_b"]);
    expect((tray.querySelector("details") as HTMLDetailsElement).open).toBe(false);
  });

  it("按别名搜索也能找到人；搜索时偶遇面孔自动展开", () => {
    mount();
    fireEvent.change(screen.getByLabelText("搜索人物"), { target: { value: "阿娟" } });
    expect(ids(tier("我的人物"))).toEqual(["mom"]);

    fireEvent.change(screen.getByLabelText("搜索人物"), { target: { value: "邻居" } });
    const tray = tier("偶遇面孔");
    expect((tray.querySelector("details") as HTMLDetailsElement).open).toBe(true);
    expect(ids(tray)).toEqual(["neighbour"]);
  });
});

describe("偶遇面孔的批量整理", () => {
  function choose(...personIds: string[]) {
    const tray = tier("偶遇面孔");
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
    vi.mocked(client.getPerson).mockImplementation(async (id: string) => ({
      entity: { aliases: [] },
      clusters: [{ id: `cluster_${id}`, status: "pending" }],
    }) as never);
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

  it("合并为同一人交给合并确认框", () => {
    mount();
    const tray = choose("once_a", "once_b");
    fireEvent.click(within(tray).getByRole("button", { name: "合并为同一人" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});
