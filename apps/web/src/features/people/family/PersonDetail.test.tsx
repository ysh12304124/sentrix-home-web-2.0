import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../shared/api/client")>();
  return {
    ...actual,
    getPerson: vi.fn(),
    getPersonPortrait: vi.fn().mockResolvedValue(null),
    getEntityDetail: vi.fn(),
    setEntityProperty: vi.fn(),
  };
});

import * as client from "../../../shared/api/client";
import type { FamilyPerson } from "../../../shared/api/client";
import { PersonDetail } from "./PersonDetail";

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
