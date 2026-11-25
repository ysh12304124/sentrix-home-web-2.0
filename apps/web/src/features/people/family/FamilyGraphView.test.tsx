import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../shared/api/client")>();
  return {
    ...actual,
    putFamilyRelationship: vi.fn(),
    deleteFamilyRelationship: vi.fn(),
  };
});

import * as client from "../../../shared/api/client";
import type { FamilyPerson, FamilyRelationship } from "../../../shared/api/client";
import { FamilyGraphView } from "./FamilyGraphView";

function person(id: string, name: string): FamilyPerson {
  return {
    id,
    scope_id: "album1",
    display_name: name,
    membership: null,
    portrait: null,
    avatar_face_instance_id: null,
    appearance_count: 1,
    representative_media: [],
    aliases: [],
    hidden: false,
    status: "confirmed",
  };
}

function relation(): FamilyRelationship {
  return {
    id: "rel_1",
    scope_id: "album1",
    family_graph_id: null,
    subject_entity_id: "person_a",
    predicate: "父亲",
    object_entity_id: "person_b",
    inverse_predicate: "儿子",
    source: "user_override",
    confidence: 1,
    evidence_refs: [],
    state: "active",
    locked: true,
    revision: 1,
  };
}

const people = [person("person_a", "爸爸"), person("person_b", "儿子")];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("关系图点边编辑", () => {
  it("点中一条边后可以改谓词，PUT 带上那条关系自己的 scope 与正反向标签", async () => {
    vi.mocked(client.putFamilyRelationship).mockResolvedValue({
      ok: true, relationships: [], sync_status: "synced", sync_error: "",
    });
    const onChanged = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <FamilyGraphView
        people={people}
        relationships={[relation()]}
        selectedId={null}
        onSelect={vi.fn()}
        onRelate={vi.fn()}
        onChanged={onChanged}
      />,
    );

    const edge = container.querySelector('[data-edge-id="person_a->person_b"]');
    expect(edge).not.toBeNull();
    fireEvent.click(edge as Element);

    const editor = await screen.findByRole("region", { name: "编辑关系" });
    // 整句：subject 是 object 的 predicate。
    expect(editor.textContent).toContain("爸爸 是 儿子 的父亲");

    // 「朋友」的反向只有「朋友」一项，界面直接填上，不再多问。
    fireEvent.change(screen.getByLabelText("关系称谓"), { target: { value: "朋友" } });
    expect(editor.textContent).toContain("将记录：爸爸 是 儿子 的朋友；儿子 是 爸爸 的朋友");
    fireEvent.click(screen.getByRole("button", { name: "保存关系" }));

    await waitFor(() => {
      expect(client.putFamilyRelationship).toHaveBeenCalledWith({
        scopeId: "album1",
        subjectEntityId: "person_a",
        predicate: "朋友",
        objectEntityId: "person_b",
        inversePredicate: "朋友",
      });
    });
    expect(onChanged).toHaveBeenCalled();
    expect(await screen.findByText("关系已保存，图谱与检索索引已同步。")).toBeTruthy();
  });

  it("删除关系只带两端 id 与 scope", async () => {
    vi.mocked(client.deleteFamilyRelationship).mockResolvedValue({
      ok: true, retracted: [], sync_status: "synced", sync_error: "",
    });
    const onChanged = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <FamilyGraphView
        people={people}
        relationships={[relation()]}
        selectedId={null}
        onSelect={vi.fn()}
        onRelate={vi.fn()}
        onChanged={onChanged}
      />,
    );

    fireEvent.click(container.querySelector('[data-edge-id="person_a->person_b"]') as Element);
    fireEvent.click(await screen.findByRole("button", { name: "删除关系" }));

    await waitFor(() => {
      expect(client.deleteFamilyRelationship).toHaveBeenCalledWith({
        scopeId: "album1",
        subjectEntityId: "person_a",
        objectEntityId: "person_b",
      });
    });
    expect(onChanged).toHaveBeenCalled();
    expect(await screen.findByText("关系已保存，图谱与检索索引已同步。")).toBeTruthy();
  });
});

describe("关系图交互", () => {
  const trio = [...people, person("person_c", "邻居")];

  function mountGraph(overrides: Partial<Parameters<typeof FamilyGraphView>[0]> = {}) {
    const props = {
      people: trio,
      relationships: [relation()],
      selectedId: null,
      onSelect: vi.fn(),
      onRelate: vi.fn(),
      onChanged: vi.fn().mockResolvedValue(undefined),
      ...overrides,
    };
    const view = render(<FamilyGraphView {...props} />);
    return { ...view, props };
  }

  /** 读 `<g transform="translate(x y) scale(k)">` 与节点的 translate，换算出节点的屏幕坐标。 */
  function screenOf(container: HTMLElement, id: string): { x: number; y: number } {
    const view = container.querySelector(".graph-svg > g")?.getAttribute("transform") ?? "";
    const [, tx, ty, k] = /translate\(([-\d.e]+) ([-\d.e]+)\) scale\(([-\d.e]+)\)/.exec(view) ?? [];
    const node = container.querySelector(`[data-node-id="${id}"]`)?.getAttribute("transform") ?? "";
    const [, nx, ny] = /translate\(([-\d.e]+) ([-\d.e]+)\)/.exec(node) ?? [];
    return { x: Number(tx) + Number(k) * Number(nx), y: Number(ty) + Number(k) * Number(ny) };
  }

  function drag(from: Element, start: { x: number; y: number }, end: { x: number; y: number }) {
    fireEvent.pointerDown(from, { clientX: start.x, clientY: start.y });
    fireEvent.pointerMove(window, { clientX: (start.x + end.x) / 2, clientY: (start.y + end.y) / 2 });
    fireEvent.pointerMove(window, { clientX: end.x, clientY: end.y });
    fireEvent.pointerUp(window, { clientX: end.x, clientY: end.y });
  }

  it("还没有关系的人也在图上，并带着 ⊕ 把手", () => {
    const { container } = mountGraph();
    expect(container.querySelector('[data-node-id="person_c"]')).not.toBeNull();
    expect(container.querySelector('[data-relate-handle="person_c"]')).not.toBeNull();
  });

  it("从 ⊕ 拖到另一个人身上：触发建关系", () => {
    const { container, props } = mountGraph();
    const start = screenOf(container, "person_c");
    const end = screenOf(container, "person_a");
    drag(container.querySelector('[data-relate-handle="person_c"]') as Element, start, end);
    expect(props.onRelate).toHaveBeenCalledWith("person_c", "person_a");
  });

  it("单击人物是选中；拖动人物是摆位置，不建关系、不选中", () => {
    const { container, props } = mountGraph();
    const node = container.querySelector('[data-node-id="person_c"]') as Element;
    const start = screenOf(container, "person_c");
    fireEvent.pointerDown(node, { clientX: start.x, clientY: start.y });
    fireEvent.pointerUp(window, { clientX: start.x, clientY: start.y });
    expect(props.onSelect).toHaveBeenCalledWith("person_c");

    vi.mocked(props.onSelect).mockClear();
    drag(node, start, { x: start.x + 120, y: start.y + 60 });
    expect(props.onSelect).not.toHaveBeenCalled();
    expect(props.onRelate).not.toHaveBeenCalled();
    expect(container.querySelector('[data-node-id="person_c"]')?.getAttribute("class")).toContain("pinned");
    expect(screen.getByRole("button", { name: "重新排布" })).toBeTruthy();
  });

  it("以选中的人为中心：TA 在圆心，没选人时这个视图不可用", () => {
    const { container, rerender, props } = mountGraph();
    const focusTab = screen.getByRole("tab", { name: "以选中的人为中心" }) as HTMLButtonElement;
    expect(focusTab.disabled).toBe(true);

    rerender(<FamilyGraphView {...props} selectedId="person_b" />);
    fireEvent.click(screen.getByRole("tab", { name: "以 儿子 为中心" }));
    expect(container.querySelector('[data-node-id="person_b"]')?.getAttribute("transform")).toBe("translate(0 0)");
    // 和儿子没有关系的邻居在最外圈并变淡。
    expect(container.querySelector('[data-node-id="person_c"]')?.getAttribute("class")).toContain("dim");
  });

  it("双击一个人：选中并切到以 TA 为中心", () => {
    const { container, props } = mountGraph();
    fireEvent.doubleClick(container.querySelector('[data-node-id="person_a"]') as Element);
    expect(props.onSelect).toHaveBeenCalledWith("person_a");
  });

  it("对称称谓只贴一个标签在中间", () => {
    const friends = { ...relation(), predicate: "朋友", inverse_predicate: "朋友" };
    const { container } = mountGraph({ relationships: [friends], selectedId: "person_a" });
    const labels = [...container.querySelectorAll(".graph-edge-label")];
    expect(labels).toHaveLength(1);
  });
});
