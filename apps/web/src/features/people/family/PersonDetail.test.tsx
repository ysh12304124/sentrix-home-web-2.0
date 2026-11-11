import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../shared/api/client")>();
  return {
    ...actual,
    getPerson: vi.fn().mockResolvedValue({ entity: { aliases: [] } }),
    getPersonPortrait: vi.fn().mockResolvedValue(null),
    getEntityDetail: vi.fn(),
    setEntityProperty: vi.fn(),
    putFamilyRelationship: vi.fn().mockResolvedValue([]),
  };
});

import * as client from "../../../shared/api/client";
import type { FamilyPerson } from "../../../shared/api/client";
import { PersonDetail } from "./PersonDetail";

describe("PersonDetail 建立关系的空间隔离", () => {
  function mount(relateTarget: string | null = null) {
    const onChanged = vi.fn().mockResolvedValue(undefined);
    render(
      <PersonDetail
        person={person()}
        people={[
          person(),
          person({ id: "same_scope", display_name: "孩子" }),
          person({ id: "other_scope", scope_id: "album2", display_name: "其他空间人物" }),
        ]}
        relationships={[]}
        relateTarget={relateTarget}
        onClose={() => undefined}
        onChanged={onChanged}
      />,
    );
    return onChanged;
  }

  it("只提供同空间的其他人物，并用人物自己的空间创建关系", async () => {
    const onChanged = mount();
    const target = screen.getByLabelText("对方");
    expect(target.querySelector('option[value="person_1"]')).toBeNull();
    expect(target.querySelector('option[value="other_scope"]')).toBeNull();
    expect(target.querySelector('option[value="same_scope"]')).not.toBeNull();

    fireEvent.change(target, { target: { value: "same_scope" } });
    fireEvent.click(screen.getByRole("button", { name: "建立关系" }));

    await waitFor(() => expect(client.putFamilyRelationship).toHaveBeenCalledWith({
      scopeId: "album1",
      subjectEntityId: "person_1",
      predicate: "父亲",
      objectEntityId: "same_scope",
      inversePredicate: "儿子",
    }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
  });

  it("跨空间的拖拽预选不能提交关系", () => {
    mount("other_scope");
    const button = screen.getByRole("button", { name: "建立关系" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    // 直接提交表单也不能绕过空间检查。
    fireEvent.submit(button.closest("form") as HTMLFormElement);
    expect(client.putFamilyRelationship).not.toHaveBeenCalled();
  });
});

function person(overrides: Partial<FamilyPerson> = {}): FamilyPerson {
  return {
    id: "person_1",
    scope_id: "album1",
    display_name: "妈妈",
    membership: null,
    portrait: null,
    avatar_face_instance_id: null,
    appearance_count: 3,
    representative_media: [],
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PersonDetail 别名编辑", () => {
  it("从 GET /people/{id} 读别名，保存时写 entity_properties 的 aliases 键", async () => {
    vi.mocked(client.getPerson).mockResolvedValue({
      entity: {
        id: "person_1",
        scope_id: "album1",
        canonical_name: "妈妈",
        status: "confirmed",
        family_role: "母亲",
        summary: "",
        confidence: 1,
        confirmed: true,
        aliases: ["小明", "阿明"],
      },
      avatar_face_instance_id: null,
      clusters: [],
    });

    render(
      <PersonDetail
        person={person()}
        people={[person()]}
        relationships={[]}
        onClose={() => undefined}
        onChanged={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "编辑别名" }));
    const input = await screen.findByLabelText("人物别名");
    expect((input as HTMLInputElement).value).toBe("小明、阿明");

    fireEvent.change(input, { target: { value: "阿明，明明、阿明" } });
    fireEvent.click(screen.getByRole("button", { name: "保存别名" }));

    await waitFor(() => {
      expect(client.setEntityProperty).toHaveBeenCalledWith("person_1", "aliases", [
        "阿明",
        "明明",
      ]);
    });
  });
});
