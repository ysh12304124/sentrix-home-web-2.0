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
    expect(editor.textContent).toContain("爸爸 · 父亲 → 儿子");

    // 词表第 9 项是「朋友 → 朋友」（索引 8）
    fireEvent.change(screen.getByLabelText("关系类型"), { target: { value: "8" } });
    fireEvent.click(screen.getByRole("button", { name: "保存谓词" }));

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
