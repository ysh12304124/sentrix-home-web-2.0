/**
 * `/people` 冒烟——钉住计划书 §0 的核心结论。
 *
 * 实测那份真实数据是 **8 个人、0 条关系、membership 全 unknown**。1.0 的图谱只收
 * family/friend，在这个形状下会画出一张空图，还把待归类的人藏进另一个 tab。
 * 所以这里的断言不是「能渲染」，而是**「8 个人必须出现 8 张卡，而不是空状态」**。
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    getFamilyGraph: vi.fn(),
    listMemorySpaces: vi.fn(),
    listPeople: vi.fn(),
    rejectFaceCluster: vi.fn(),
    renamePerson: vi.fn(),
    getPerson: vi.fn().mockResolvedValue({ entity: { aliases: [] } }),
    getPersonPortrait: vi.fn().mockResolvedValue(null),
    putFamilyRelationship: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import type { FamilyGraph, FamilyPerson, FamilyRelationship } from "../../shared/api/client";
import { ScopeProvider } from "../../app/ScopeProvider";
import { PeoplePage } from "./PeoplePage";

/** 后端一条关系落**两行**（正行 + 反行）。夹具按这个形状造，才测得到归并。 */
function relationRow(
  subject: string,
  predicate: string,
  inverse: string,
  object: string,
): FamilyRelationship {
  return {
    id: `r_${subject}_${object}_${predicate}`,
    scope_id: "album1",
    family_graph_id: null,
    subject_entity_id: subject,
    predicate,
    object_entity_id: object,
    inverse_predicate: inverse,
    source: "user_override",
    confidence: 1,
    evidence_refs: [],
    state: "active",
    locked: true,
    revision: 1,
  };
}

function withMembership(person: FamilyPerson, membership: string): FamilyPerson {
  return {
    ...person,
    membership: { ...(person.membership as NonNullable<FamilyPerson["membership"]>), membership },
  };
}

/** 一份「8 个人、0 条关系、全 unknown」的真实形状。 */
function realShapeGraph(): FamilyGraph {
  const people: FamilyPerson[] = Array.from({ length: 8 }, (_, index) => ({
    id: `person_${index}`,
    scope_id: "album1",
    display_name: `人物${index}`,
    // 实测里 membership 是行对象但 membership 字段为 "unknown"；null 的那条分支由
    // 布局单测覆盖（graphLayout.test.ts）。
    membership: {
      id: `m_${index}`,
      scope_id: "album1",
      entity_id: `person_${index}`,
      membership: "unknown",
      source: "model",
      confidence: 0,
      evidence_refs: [],
      inference_run_id: "run1",
      state: "active",
      locked: false,
      revision: 1,
      supersedes_id: null,
      created_at: "2026-10-02T00:00:00+00:00",
      updated_at: "2026-10-02T00:00:00+00:00",
    },
    portrait: null,
    avatar_face_instance_id: null,
    appearance_count: 1,
    representative_media: [],
    aliases: [],
    hidden: false,
    status: "confirmed",
  }));
  return { scope_id: "album1", run: null, people, scope_ids: ["album1"], relationships: [] };
}

function mount(graph: FamilyGraph = realShapeGraph(), search = "/?scope=album1") {
  vi.mocked(client.listMemorySpaces).mockResolvedValue([
    { id: "album1", name: "第一册", kind: "benchmark", status: "active" },
  ]);
  vi.mocked(client.getFamilyGraph).mockResolvedValue(graph);
  vi.mocked(client.listPeople).mockResolvedValue([]);
  // ScopeProvider 优先读 `?scope=`；设上它，取数才是 album1（而不是「全部相册」）。
  window.history.replaceState(null, "", search);
  return render(
    // `initialEntries` 与 `window.history` 都要设：ScopeProvider 读 window.location，
    // 而 `useSearchParams`（`?person=`）读的是 router 自己的 location，两者不是一份。
    <MemoryRouter initialEntries={[search]}>
      <ScopeProvider>
        <PeoplePage />
      </ScopeProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/");
});

describe("/people 名人录（F7 §0 真实形状）", () => {
  it("保存完成后移除姓名预览，较晚返回的旧轮询不会覆盖新姓名", async () => {
    let finish!: (value: Awaited<ReturnType<typeof client.renamePerson>>) => void;
    let oldRead!: (value: FamilyGraph) => void;
    vi.mocked(client.renamePerson).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const graph = realShapeGraph();
    const updated = { ...graph, people: graph.people.map((person) => person.id === "person_0"
      ? { ...person, display_name: "服务端新姓名" } : person) };
    const { container } = mount(graph, "/?scope=album1&person=person_0");
    await screen.findByRole("complementary");
    vi.mocked(client.getFamilyGraph).mockImplementationOnce(() => new Promise((resolve) => { oldRead = resolve; }));
    vi.mocked(client.getFamilyGraph).mockResolvedValue(updated);
    fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "输入预览姓名" } });
    fireEvent.click(screen.getByRole("button", { name: "保存姓名" }));
    expect(container.querySelector('[data-person-id="person_0"]')?.textContent).toContain("输入预览姓名");
    await waitFor(() => expect(oldRead).toBeDefined(), { timeout: 3000 });
    await act(async () => finish({
      entity: { id: "person_0", scope_id: "album1", canonical_name: "服务端新姓名", status: "confirmed",
        family_role: null, summary: "", confidence: 1, confirmed: true, aliases: [] },
      aliases: [], sync_status: "synced", sync_error: "",
    }));
    await waitFor(() => expect(container.querySelector('[data-person-id="person_0"]')?.textContent).toContain("服务端新姓名"));
    expect(screen.queryByText(/正在保存并重建关联记忆/)).toBeNull();
    await act(async () => oldRead(graph));
    expect(container.querySelector('[data-person-id="person_0"]')?.textContent).toContain("服务端新姓名");
    expect(screen.getByRole("complementary", { name: "服务端新姓名 的详情" })).toBeTruthy();
  });

  it("保存姓名时卡片和详情立即显示新姓名，重建失败则恢复服务端姓名", async () => {
    let fail!: (error: Error) => void;
    vi.mocked(client.renamePerson).mockImplementation(() => new Promise((_resolve, reject) => { fail = reject; }));
    const graph = realShapeGraph();
    const { container } = mount(graph, "/?scope=album1&person=person_0");
    await screen.findByRole("complementary");
    fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "新姓名" } });
    fireEvent.click(screen.getByRole("button", { name: "保存姓名" }));
    expect(container.querySelector('[data-person-id="person_0"]')?.textContent).toContain("新姓名");
    expect(screen.getByRole("complementary", { name: "新姓名 的详情" })).toBeTruthy();
    expect(screen.getByText(/正在保存并重建关联记忆/)).toBeTruthy();
    fail(new Error("保存失败"));
    expect(await screen.findByRole("alert")).toBeTruthy();
    await waitFor(() => expect(container.querySelector('[data-person-id="person_0"]')?.textContent).toContain("人物0"));
  });
  it("8 个人 0 条关系时渲染 8 张人物卡，不是空状态", async () => {
    const { container } = mount();

    await waitFor(() => {
      expect(container.querySelectorAll("[data-person-id]")).toHaveLength(8);
    });
    // 「有没有内容」由人数决定，关系条数为 0 不算空——这正是 1.0 画空图的那个坑。
    expect(screen.queryByText("还没有可显示的人物")).toBeNull();
    // 数字跟着各自的 tab：人物 8、关系 0。
    expect(screen.getByRole("tab", { name: "人物8" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "关系0" })).toBeTruthy();
    // 默认视图是名录（计划书裁决②），图谱 tab 此时不该被卸载掉门槛
    expect(screen.getByRole("tab", { name: /^人物/ }).getAttribute("aria-selected")).toBe("true");
  });

  it("点一张卡走 ?person= 打开详情侧栏（不加路由）", async () => {
    const { container } = mount();
    await waitFor(() => {
      expect(container.querySelectorAll("[data-person-id]")).toHaveLength(8);
    });

    fireEvent.click(container.querySelectorAll("[data-person-id]")[0]);

    // 详情是一个 aside，不再多一条路由；组件的取数/渲染都走查询参数
    expect(await screen.findByRole("complementary")).toBeTruthy();
    expect(container.querySelectorAll(".person-detail")).toHaveLength(1);
  });
});

/** 图谱 tab 的渲染覆盖。此前图谱组件在任何测试里都没挂载过：`fit()` 的居中项、
 *  缩放的不动点、拖拽命中判定写错都只会让画面错位，不会抛错。 */
describe("图谱 tab", () => {
  function transformOf(container: HTMLElement): string | null {
    return container.querySelector(".graph-svg > g")?.getAttribute("transform") ?? null;
  }

  async function openGraph(container: HTMLElement) {
    fireEvent.click(screen.getByRole("tab", { name: /^关系/ }));
    await waitFor(() => {
      expect(container.querySelector(".graph-shell, .graph-empty")).not.toBeNull();
    });
  }

  it("全部相册中创建同空间关系后，详情、计数与关系图都从刷新结果更新", async () => {
    const base = realShapeGraph();
    const dad = { ...base.people[0], display_name: "爸爸" };
    const son = { ...base.people[1], display_name: "儿子" };
    const other = { ...base.people[2], scope_id: "album2" };
    const graph = { ...base, scope_id: "", people: [dad, son, other], scope_ids: ["album1", "album2"] };
    const rows = [
      relationRow(dad.id, "父亲", "儿子", son.id),
      relationRow(son.id, "儿子", "父亲", dad.id),
    ];
    vi.mocked(client.putFamilyRelationship).mockImplementation(async () => {
      vi.mocked(client.getFamilyGraph).mockResolvedValue({ ...graph, relationships: rows });
      return { ok: true, relationships: rows, sync_status: "synced", sync_error: "" };
    });
    const { container } = mount(graph, `/?person=${dad.id}`);
    await screen.findByRole("complementary");
    // 「儿子 是 爸爸 的儿子」→ 反向有父亲/母亲两项，选「父亲」。
    fireEvent.click(container.querySelector(`[data-option-id="${son.id}"]`) as Element);
    fireEvent.change(screen.getByLabelText("关系称谓"), { target: { value: "儿子" } });
    fireEvent.click(screen.getByRole("button", { name: "父亲" }));
    fireEvent.click(screen.getByRole("button", { name: "建立关系" }));

    await screen.findByRole("tab", { name: "关系1" });
    expect(client.putFamilyRelationship).toHaveBeenCalledWith({
      scopeId: "album1",
      subjectEntityId: son.id,
      predicate: "儿子",
      objectEntityId: dad.id,
      inversePredicate: "父亲",
    });
    expect(client.getFamilyGraph).toHaveBeenLastCalledWith("");
    expect(container.querySelectorAll(".detail-relations li")).toHaveLength(1);
    await openGraph(container);
    expect(container.querySelectorAll(".graph-edge")).toHaveLength(1);
    // 认出来的人都在图上：爸爸、儿子，以及另一个相册里还没有关系的那位（散在外围）。
    expect(container.querySelectorAll("[data-node-id]")).toHaveLength(3);
  });

  it("没有已确认关系时人照样在图上，并提示怎么建第一条关系", async () => {
    const { container } = mount();
    await openGraph(container);

    expect(screen.getByText(/还没有关系：把鼠标移到一个人身上/)).toBeTruthy();
    // 8 个认出来的人都在图上，每人都有拖出关系的 ⊕ 把手。
    expect(container.querySelectorAll("[data-node-id]")).toHaveLength(8);
    expect(container.querySelectorAll("[data-relate-handle]")).toHaveLength(8);
    expect(container.querySelectorAll(".graph-edge")).toHaveLength(0);
  });

  it("后端一次写两行 + 一条同对的其他关系：图上只画 1 条边，且是优先级最高的那条", async () => {
    const base = realShapeGraph();
    const dad = withMembership({ ...base.people[0], display_name: "爸爸" }, "family");
    const son = withMembership({ ...base.people[1], display_name: "儿子" }, "family");
    // 三条输入行都落在同一对人身上：正行、反行、以及一条更低优先级的关系，且胜者
    // 不在最后——「不归并」会画 3 条边，「保留最后一条」会画成「朋友」，两种都能现形。
    // 选中爸爸，边的标签才会渲染，于是「画的是哪条边」也进得了断言。
    const { container } = mount(
      {
        ...base,
        people: [dad, son],
        relationships: [
          relationRow(dad.id, "父亲", "儿子", son.id),
          relationRow(son.id, "儿子", "父亲", dad.id),
          relationRow(dad.id, "朋友", "朋友", son.id),
        ],
      },
      `/?scope=album1&person=${dad.id}`,
    );
    await openGraph(container);

    await waitFor(() => {
      expect(container.querySelectorAll("[data-node-id]")).toHaveLength(2);
    });
    expect(container.querySelectorAll(".graph-edge")).toHaveLength(1);
    // 两端各贴一个称谓：「父亲」贴爸爸、「儿子」贴孩子（<title> 里是整句，不算标签文字）。
    const labels = [...container.querySelectorAll(".graph-edge-label")].map(
      (node) => node.childNodes[node.childNodes.length - 1]?.textContent,
    );
    expect(labels).toEqual(["父亲", "儿子"]);
    const transform = transformOf(container);
    expect(transform).not.toBeNull();
    expect(transform).not.toContain("NaN");
  });
});
