import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    listPersonInsights: vi.fn(),
    sendPortraitFeedback: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import type { PersonInsightsResponse } from "../../shared/api/client";
import { PersonInsights } from "./PersonInsights";

function insights(): PersonInsightsResponse {
  return {
    scope_id: "album1",
    tiers: {
      core: [
        {
          person_id: "person_1",
          display_name: "妈妈",
          family_role: "母亲",
          status: "confirmed",
          avatar_face_instance_id: "face_1",
          date_count: 6,
          event_count: 3,
          portrait: {
            id: "portrait_1",
            person_id: "person_1",
            revision: 2,
            portrait_text: "喜欢在周末带家人去海边。",
            themes: ["家庭"],
            evidence_refs: [],
            status: "active",
            trigger_type: "ingest",
            model_name: "local",
            prompt_version: "v1",
            feedback: null,
          },
        },
      ],
      common: [],
      incidental: [],
    },
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("画像治理", () => {
  it("展示当前画像，并把「像他」作为 feedback 文本提交", async () => {
    vi.mocked(client.listPersonInsights).mockResolvedValue(insights());
    vi.mocked(client.sendPortraitFeedback).mockResolvedValue(
      insights().tiers.core[0].portrait as NonNullable<
        PersonInsightsResponse["tiers"]["core"][number]["portrait"]
      >,
    );

    render(<PersonInsights scopeId="album1" />);

    expect(await screen.findByText("喜欢在周末带家人去海边。")).toBeTruthy();
    expect(screen.getByText(/6 天 · 3 个事件/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "像他" }));

    await waitFor(() => {
      expect(client.sendPortraitFeedback).toHaveBeenCalledWith("person_1", "像他");
    });
    // 后端没有锁定/编辑画像端点：界面不应给出会 405 的按钮。
    expect(screen.queryByRole("button", { name: "锁定" })).toBeNull();
    expect(screen.queryByRole("button", { name: "编辑画像" })).toBeNull();
  });

  it("已锁定画像显示徽标", async () => {
    const data = insights();
    const portrait = data.tiers.core[0].portrait;
    if (portrait !== null) portrait.status = "user_locked";
    vi.mocked(client.listPersonInsights).mockResolvedValue(data);

    render(<PersonInsights scopeId="album1" />);
    expect(await screen.findByText("已锁定")).toBeTruthy();
  });
});
