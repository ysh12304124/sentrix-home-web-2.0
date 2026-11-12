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
    setPersonHidden: vi.fn().mockResolvedValue(undefined),
  };
});

import * as client from "../../../shared/api/client";
import type { FamilyPerson, FamilyRelationship } from "../../../shared/api/client";
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
    const options = document.querySelector('[aria-label="对方"]') as HTMLElement;
    expect(options.querySelector('[data-option-id="person_1"]')).toBeNull();
    expect(options.querySelector('[data-option-id="other_scope"]')).toBeNull();
    const target = options.querySelector('[data-option-id="same_scope"]') as HTMLElement;
    expect(target).not.toBeNull();

    fireEvent.click(target);
    // 「孩子 是 妈妈 的儿子」：反向有父亲/母亲两项，必须再选一次才能提交。
    fireEvent.change(screen.getByLabelText("关系"), { target: { value: "儿子" } });
    const submit = screen.getByRole("button", { name: "建立关系" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: "母亲" }));
    expect(screen.getByText("将记录：孩子 是 妈妈 的儿子；妈妈 是 孩子 的母亲")).toBeTruthy();
    fireEvent.click(submit);

    await waitFor(() => expect(client.putFamilyRelationship).toHaveBeenCalledWith({
      scopeId: "album1",
      subjectEntityId: "same_scope",
      predicate: "儿子",
      objectEntityId: "person_1",
      inversePredicate: "母亲",
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
    aliases: [],
    hidden: false,
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

describe("PersonDetail 别名编辑的取消与快捷键", () => {
  async function openAliasEditor() {
    vi.mocked(client.getPerson).mockResolvedValue({
      entity: {
        id: "person_1",
        scope_id: "album1",
        canonical_name: "妈妈",
        status: "confirmed",
        family_role: null,
        summary: "",
        confidence: 1,
        confirmed: true,
        aliases: ["阿娟"],
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
    const input = (await screen.findByLabelText("人物别名")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "阿娟、老妈" } });
    return input;
  }

  it("点「取消」退出编辑，不保存；再打开时是原来的内容", async () => {
    await openAliasEditor();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(screen.queryByLabelText("人物别名")).toBeNull();
    expect(client.setEntityProperty).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "编辑别名" }));
    expect(((await screen.findByLabelText("人物别名")) as HTMLInputElement).value).toBe("阿娟");
  });

  it("Esc 取消、Enter 保存", async () => {
    let input = await openAliasEditor();
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByLabelText("人物别名")).toBeNull();
    expect(client.setEntityProperty).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "编辑别名" }));
    input = (await screen.findByLabelText("人物别名")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "阿娟、老妈" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(client.setEntityProperty).toHaveBeenCalledWith("person_1", "aliases", ["阿娟", "老妈"]),
    );
  });

  it("输入法组字时按 Enter / Esc 交给输入法，不保存也不取消", async () => {
    const input = await openAliasEditor();
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    fireEvent.keyDown(input, { key: "Escape", keyCode: 229 });

    expect(client.setEntityProperty).not.toHaveBeenCalled();
    expect(screen.getByLabelText("人物别名")).toBeTruthy();
  });

  it("改了姓名可以取消回原名；Esc 同样回原名", () => {
    render(
      <PersonDetail
        person={person()}
        people={[person()]}
        relationships={[]}
        onClose={() => undefined}
        onChanged={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    const input = screen.getByLabelText("姓名") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "妈" } });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(input.value).toBe("妈妈");

    fireEvent.change(input, { target: { value: "妈" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input.value).toBe("妈妈");
    expect(client.renamePerson).not.toHaveBeenCalled();
  });
});

describe("PersonDetail 关系双向显示", () => {
  const dad = () => person({ id: "dad", display_name: "爸爸" });
  const son = () => person({ id: "son", display_name: "儿子" });
  // 后端一条关系落两行：正行「爸爸 是 儿子 的父亲」+ 反行「儿子 是 爸爸 的儿子」。
  const rows: FamilyRelationship[] = [
    relationRow("dad", "父亲", "儿子", "son"),
    relationRow("son", "儿子", "父亲", "dad"),
  ];

  function mountFor(self: FamilyPerson) {
    render(
      <PersonDetail
        person={self}
        people={[dad(), son()]}
        relationships={rows}
        onClose={() => undefined}
        onChanged={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    return document.querySelector(".detail-relations") as HTMLElement;
  }

  it("在父亲的档案里，对方显示为儿子", () => {
    expect(mountFor(dad()).textContent).toContain("儿子 是 爸爸 的儿子");
  });

  it("在儿子的档案里，对方显示为父亲（同一条关系，另一侧自动可见）", () => {
    expect(mountFor(son()).textContent).toContain("爸爸 是 儿子 的父亲");
  });

  it("选中已有关系的人时提示将替换", () => {
    mountFor(son());
    fireEvent.click(document.querySelector('[data-option-id="dad"]') as Element);
    expect(screen.getByText("将替换现有关系：爸爸 是 儿子 的父亲")).toBeTruthy();
  });
});

describe("PersonDetail 标为路人", () => {
  it("待确认的人可以标为路人；已标的可以取消", async () => {
    const onChanged = vi.fn().mockResolvedValue(undefined);
    const pending = person({ status: "pending", display_name: "未确认姓名人物" });
    const { rerender } = render(
      <PersonDetail
        person={pending}
        people={[pending]}
        relationships={[]}
        onClose={() => undefined}
        onChanged={onChanged}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "标为路人" }));
    await waitFor(() => expect(client.setPersonHidden).toHaveBeenCalledWith("person_1", true));
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());

    const hidden = { ...pending, hidden: true };
    rerender(
      <PersonDetail
        person={hidden}
        people={[hidden]}
        relationships={[]}
        onClose={() => undefined}
        onChanged={onChanged}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "取消路人标记" }));
    await waitFor(() => expect(client.setPersonHidden).toHaveBeenCalledWith("person_1", false));
  });
});

function relationRow(subject: string, predicate: string, inverse: string, object: string): FamilyRelationship {
  return {
    id: `r_${subject}_${object}`,
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
  } as FamilyRelationship;
}

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
