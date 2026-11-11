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
    batchConfirmPeople: vi.fn(),
    rejectFaceCluster: vi.fn().mockResolvedValue({ cluster_id: "cluster_1", rejected_entity_id: "person_1" }),
    renamePerson: vi.fn(),
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
    status: "confirmed",
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

describe("PersonDetail 待确认人物", () => {
  function mountPending(overrides: Partial<FamilyPerson> = {}) {
    const onChanged = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    const onRedirect = vi.fn();
    const pending = person({ status: "pending", display_name: "未确认姓名人物", ...overrides });
    render(
      <PersonDetail
        person={pending}
        people={[pending]}
        relationships={[]}
        onClose={onClose}
        onChanged={onChanged}
        onRedirect={onRedirect}
      />,
    );
    return { onChanged, onClose, onRedirect };
  }

  it("给「确认身份」而不是「改名」，并走批量确认接口", async () => {
    vi.mocked(client.batchConfirmPeople).mockResolvedValue({
      confirmed: [{ person_id: "person_1", entity_id: "person_1", name: "王王", family_role: "孩子", merged: false }],
      failed: [],
      count: 1,
    });
    const { onChanged, onRedirect } = mountPending();

    expect(screen.queryByRole("button", { name: "保存姓名" })).toBeNull();
    const input = screen.getByPlaceholderText("这个人是谁？") as HTMLInputElement;
    // 占位文案不是名字，不预填。
    expect(input.value).toBe("");
    fireEvent.change(input, { target: { value: "王王" } });
    fireEvent.change(screen.getByLabelText("家庭角色"), { target: { value: "孩子" } });
    fireEvent.click(screen.getByRole("button", { name: "确认身份" }));

    await waitFor(() =>
      expect(client.batchConfirmPeople).toHaveBeenCalledWith([
        { person_id: "person_1", name: "王王", family_role: "孩子" },
      ]),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(client.renamePerson).not.toHaveBeenCalled();
    expect(onRedirect).not.toHaveBeenCalled();
  });

  it("已命名但仍待确认的人预填名字；同名并入别人时详情跟过去", async () => {
    vi.mocked(client.batchConfirmPeople).mockResolvedValue({
      confirmed: [{ person_id: "person_1", entity_id: "person_9", name: "王王", family_role: null, merged: true }],
      failed: [],
      count: 1,
    });
    const { onRedirect } = mountPending({ display_name: "王王" });

    expect((screen.getByPlaceholderText("这个人是谁？") as HTMLInputElement).value).toBe("王王");
    fireEvent.click(screen.getByRole("button", { name: "确认身份" }));

    await waitFor(() => expect(onRedirect).toHaveBeenCalledWith("person_9"));
  });

  it("确认失败的回执显示出来，不当成成功", async () => {
    vi.mocked(client.batchConfirmPeople).mockResolvedValue({
      confirmed: [],
      failed: [{ person_id: "person_1", name: "王王", error: "entity name is required" }],
      count: 0,
    });
    const { onChanged } = mountPending({ display_name: "王王" });
    fireEvent.click(screen.getByRole("button", { name: "确认身份" }));

    expect((await screen.findByRole("alert")).textContent).toContain("entity name is required");
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("「这不是一个人物」要二次确认，拒绝它的有效人脸分组后关闭详情", async () => {
    vi.mocked(client.getPerson).mockResolvedValue({
      entity: {
        id: "person_1",
        scope_id: "album1",
        canonical_name: "待确认人物簇 · cluster_1",
        status: "pending",
        family_role: null,
        summary: "",
        confidence: 0,
        confirmed: false,
        aliases: [],
      },
      avatar_face_instance_id: null,
      clusters: [
        { ...clusterStub("cluster_old"), status: "rejected" },
        clusterStub("cluster_1"),
      ],
    });
    const { onClose } = mountPending();

    fireEvent.click(screen.getByRole("button", { name: "这不是一个人物" }));
    expect(client.rejectFaceCluster).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确定移除" }));

    await waitFor(() => expect(client.rejectFaceCluster).toHaveBeenCalledWith("cluster_1"));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  });

  it("已确认的人不给「这不是一个人物」", () => {
    render(
      <PersonDetail
        person={person()}
        people={[person()]}
        relationships={[]}
        onClose={() => undefined}
        onChanged={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(screen.queryByRole("button", { name: "这不是一个人物" })).toBeNull();
    expect(screen.getByRole("button", { name: "保存姓名" })).not.toBeNull();
  });
});

function clusterStub(id: string) {
  return {
    id,
    scope_id: "album1",
    status: "pending",
    entity_id: "person_1",
    canonical_name: null,
    family_role: null,
    entity_status: "pending",
    member_count: 3,
    confidence: 0.5,
    revision: 1,
    created_at: null,
    updated_at: null,
    reviewable: true,
    single_sample: false,
    samples: [],
  };
}
