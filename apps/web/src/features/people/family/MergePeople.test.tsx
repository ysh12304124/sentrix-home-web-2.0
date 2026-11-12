import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../shared/api/client")>();
  return {
    ...actual,
    mergePeople: vi.fn(),
    getPerson: vi.fn().mockResolvedValue({ entity: { aliases: [] } }),
    getPersonPortrait: vi.fn().mockResolvedValue(null),
  };
});

import * as client from "../../../shared/api/client";
import type { FamilyPerson } from "../../../shared/api/client";
import { FamilyRoster } from "./FamilyRoster";
import { defaultMergeTarget } from "./membership";
import { PersonDetail } from "./PersonDetail";

function person(overrides: Partial<FamilyPerson> = {}): FamilyPerson {
  return {
    id: "p1",
    scope_id: "album1",
    display_name: "妈妈",
    membership: null,
    portrait: null,
    avatar_face_instance_id: null,
    appearance_count: 5,
    representative_media: [],
    status: "confirmed",
    ...overrides,
  };
}

const MOM = person({ id: "mom", display_name: "妈妈", appearance_count: 9 });
const MOM_YOUNG = person({ id: "mom_young", display_name: "年轻的妈妈", appearance_count: 4 });
const UNNAMED = person({ id: "anon", display_name: "未确认姓名人物", status: "pending", appearance_count: 12 });
const ELSEWHERE = person({ id: "far", scope_id: "album2", display_name: "别处的人", appearance_count: 6 });

function radio(dialog: HTMLElement, id: string): HTMLInputElement {
  const element = dialog.querySelector<HTMLInputElement>(`input[type="radio"][value="${id}"]`);
  if (element === null) throw new Error(`no radio ${id}`);
  return element;
}

function card(id: string): HTMLElement {
  const element = document.querySelector<HTMLElement>(`[data-person-id="${id}"]`);
  if (element === null) throw new Error(`no card ${id}`);
  return element;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("defaultMergeTarget", () => {
  it("默认保留有名字、已确认、出现多的那位", () => {
    expect(defaultMergeTarget([UNNAMED, MOM_YOUNG, MOM])).toBe("mom");
    expect(defaultMergeTarget([UNNAMED, person({ id: "x", display_name: "未确认姓名人物", status: "pending" })])).toBe(
      "anon",
    );
  });
});

describe("名录批量合并", () => {
  function mount(people: FamilyPerson[] = [MOM, MOM_YOUNG, UNNAMED]) {
    const onSelect = vi.fn();
    const onMerged = vi.fn().mockResolvedValue(undefined);
    render(<FamilyRoster people={people} selectedId={null} onSelect={onSelect} onMerged={onMerged} />);
    return { onSelect, onMerged };
  }

  it("合并模式里点卡片是勾选，不进详情；不足两人不能合并", () => {
    const { onSelect } = mount();
    fireEvent.click(screen.getByRole("button", { name: "合并人物" }));

    fireEvent.click(card("mom"));
    expect(onSelect).not.toHaveBeenCalled();
    expect(card("mom").getAttribute("aria-pressed")).toBe("true");
    expect((screen.getByRole("button", { name: "合并所选" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(card("mom_young"));
    expect((screen.getByRole("button", { name: "合并所选" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("确认框默认保留有名字的人，提交后把其余人并进去", async () => {
    vi.mocked(client.mergePeople).mockResolvedValue({
      entity: {} as never,
      merged_person_ids: ["anon", "mom_young"],
      moved_face_count: 16,
      moved_relationship_count: 0,
      dropped_relationship_count: 0,
    });
    const { onMerged } = mount();
    fireEvent.click(screen.getByRole("button", { name: "合并人物" }));
    fireEvent.click(card("anon"));
    fireEvent.click(card("mom_young"));
    fireEvent.click(card("mom"));
    fireEvent.click(screen.getByRole("button", { name: "合并所选" }));

    const dialog = screen.getByRole("dialog");
    expect((radio(dialog, "mom")).checked).toBe(true);
    expect(dialog.textContent).toContain("合并后叫「妈妈」");
    expect(dialog.textContent).toContain("其他名字会保留为别名：年轻的妈妈");

    fireEvent.click(within(dialog).getByRole("button", { name: "确认合并" }));

    await waitFor(() => expect(client.mergePeople).toHaveBeenCalledWith("mom", ["anon", "mom_young"]));
    await waitFor(() => expect(onMerged).toHaveBeenCalledWith("mom"));
  });

  it("用户可以改选保留谁", async () => {
    vi.mocked(client.mergePeople).mockResolvedValue({} as never);
    mount();
    fireEvent.click(screen.getByRole("button", { name: "合并人物" }));
    fireEvent.click(card("mom"));
    fireEvent.click(card("mom_young"));
    fireEvent.click(screen.getByRole("button", { name: "合并所选" }));

    const dialog = screen.getByRole("dialog");
    fireEvent.click(radio(dialog, "mom_young"));
    expect(dialog.textContent).toContain("合并后叫「年轻的妈妈」");
    fireEvent.click(within(dialog).getByRole("button", { name: "确认合并" }));

    await waitFor(() => expect(client.mergePeople).toHaveBeenCalledWith("mom_young", ["mom"]));
  });

  it("跨相册的人不能一起合并", () => {
    mount([MOM, ELSEWHERE]);
    fireEvent.click(screen.getByRole("button", { name: "合并人物" }));
    fireEvent.click(card("mom"));
    fireEvent.click(card("far"));

    expect((screen.getByRole("button", { name: "合并所选" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("region", { name: "合并人物" }).textContent).toContain("只能合并同一相册里的人物");
  });

  it("合并失败时显示后端的错误，不关闭确认框", async () => {
    vi.mocked(client.mergePeople).mockRejectedValue(
      new client.ApiError(409, { code: "conflict", message: "people must belong to the same memory space", detail: "" }, ""),
    );
    const { onMerged } = mount();
    fireEvent.click(screen.getByRole("button", { name: "合并人物" }));
    fireEvent.click(card("mom"));
    fireEvent.click(card("mom_young"));
    fireEvent.click(screen.getByRole("button", { name: "合并所选" }));
    fireEvent.click(screen.getByRole("button", { name: "确认合并" }));

    expect((await screen.findByRole("alert")).textContent).toContain("当前状态不允许这一步");
    expect(screen.getByRole("dialog")).not.toBeNull();
    expect(onMerged).not.toHaveBeenCalled();
  });
});

describe("详情里的「与其他人合并…」", () => {
  it("只列同空间的人，选中后走同一个确认框，保留别人时详情跟过去", async () => {
    vi.mocked(client.mergePeople).mockResolvedValue({} as never);
    const onChanged = vi.fn().mockResolvedValue(undefined);
    const onRedirect = vi.fn();
    render(
      <PersonDetail
        person={MOM_YOUNG}
        people={[MOM, MOM_YOUNG, ELSEWHERE]}
        relationships={[]}
        onClose={() => undefined}
        onChanged={onChanged}
        onRedirect={onRedirect}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "与其他人合并…" }));
    const candidates = document.querySelector(".detail-merge-candidates") as HTMLElement;
    expect(candidates.textContent).toContain("妈妈");
    expect(candidates.textContent).not.toContain("别处的人");

    fireEvent.click(within(candidates).getByRole("button", { name: /妈妈/ }));
    const dialog = screen.getByRole("dialog");
    // 默认保留出现更多、同样有名字的「妈妈」。
    expect((radio(dialog, "mom")).checked).toBe(true);
    fireEvent.click(within(dialog).getByRole("button", { name: "确认合并" }));

    await waitFor(() => expect(client.mergePeople).toHaveBeenCalledWith("mom", ["mom_young"]));
    await waitFor(() => expect(onRedirect).toHaveBeenCalledWith("mom"));
    expect(onChanged).toHaveBeenCalledOnce();
  });
});
