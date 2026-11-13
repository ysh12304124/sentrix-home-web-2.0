import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("../../api/client.ts", () => ({api: vi.fn()}));
import { api } from "../../api/client.ts";
import { VideoComparisonPanel } from "./VideoComparisonPanel.tsx";
afterEach(() => {cleanup(); vi.mocked(api).mockReset();});
it("renders persisted metrics and searchable paired answers without Judge scores", async () => {
  vi.mocked(api).mockResolvedValue({status: "ready", baseline_run_id: "old", definition: "相同题目",
    metrics: [{name: "正确关键帧", old: 1, new: 2, total: 2, gained: 1, lost: 0}],
    questions: [{qa_id: "q1", question: "婚礼视频", reference_answer: "参考", old_answer: "旧回答", new_answer: "新回答",
      old_keyframe_hit: false, new_keyframe_hit: true, old_correct_video_delivered: false, new_correct_video_delivered: true},
    {qa_id: "q2", question: "采摘视频", reference_answer: "参考", old_answer: "旧回答", new_answer: "新回答",
      old_keyframe_hit: true, new_keyframe_hit: true, old_correct_video_delivered: false, new_correct_video_delivered: false}]});
  render(<VideoComparisonPanel runId="current" />);
  expect(await screen.findByText("1/2，50.0%")).toBeTruthy();
  expect(screen.getByText("2/2，100.0%")).toBeTruthy();
  fireEvent.click(screen.getByLabelText("只看链路变化题"));
  expect(screen.getByText("显示 1 题")).toBeTruthy();
  expect(screen.queryByText("q2 · 采摘视频")).toBeNull();
  fireEvent.change(screen.getByLabelText("搜索视频对照题"), {target: {value: "不存在"}});
  expect(screen.getByText("显示 0 题")).toBeTruthy();
});
it("clears the previous comparison when switching to a run with no baseline", async () => {
  vi.mocked(api).mockResolvedValueOnce({status: "ready", metrics: [], questions: []}).mockResolvedValueOnce({status: "not_available"});
  const view = render(<VideoComparisonPanel runId="first" />);
  await screen.findByRole("region", {name: "视频链路历史对照"});
  view.rerender(<VideoComparisonPanel runId="second" />);
  await waitFor(() => expect(screen.queryByRole("region", {name: "视频链路历史对照"})).toBeNull());
  expect(vi.mocked(api).mock.calls.at(-1)?.[0]).toBe("/runs/second/video-comparison");
});
