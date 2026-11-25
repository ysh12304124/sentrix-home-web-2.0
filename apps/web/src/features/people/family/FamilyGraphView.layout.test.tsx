import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("./forceLayout", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./forceLayout")>();
  return { ...actual, forceLayout: vi.fn(actual.forceLayout) };
});

import type { FamilyPerson, FamilyRelationship } from "../../../shared/api/client";
import { FamilyGraphView } from "./FamilyGraphView";
import { forceLayout } from "./forceLayout";

const person = (id: string, name: string): FamilyPerson => ({
  id, scope_id: "album1", display_name: name, membership: null, portrait: null, avatar_face_instance_id: null,
  appearance_count: 1, representative_media: [], aliases: [], hidden: false, status: "confirmed",
});

const relation = (predicate: string): FamilyRelationship => ({
  id: "rel_1", scope_id: "album1", family_graph_id: null, subject_entity_id: "a", predicate,
  object_entity_id: "b", inverse_predicate: "儿子", source: "user_override", confidence: 1,
  evidence_refs: [], state: "effective", locked: true, revision: 1,
});

afterEach(() => {
  cleanup();
  vi.mocked(forceLayout).mockClear();
});

it("reuses the layout when a refetch only renames people, and relays out when the edges change", () => {
  const view = (people: FamilyPerson[], rows: FamilyRelationship[]) => (
    <FamilyGraphView people={people} relationships={rows} selectedId={null} onSelect={() => {}}
      onRelate={() => {}} onChanged={async () => {}} />
  );
  const { rerender } = render(view([person("a", "老周"), person("b", "小周")], [relation("父亲")]));
  const runs = vi.mocked(forceLayout).mock.calls.length;
  expect(runs).toBeGreaterThan(0);

  // 改名 / 后台对账：新数组、同样的结构——不重跑力导向。
  rerender(view([person("a", "周老"), person("b", "小周")], [relation("父亲")]));
  expect(vi.mocked(forceLayout).mock.calls.length).toBe(runs);

  rerender(view([person("a", "周老"), person("b", "小周")], [relation("干爹")]));
  expect(vi.mocked(forceLayout).mock.calls.length).toBeGreaterThan(runs);
});
