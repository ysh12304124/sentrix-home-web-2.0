import { describe, expect, it } from "vitest";

import type { Run } from "../../api/types.ts";
import { filterGraphBaselineRuns } from "./baselineRuns.ts";

const CONTEXT = {
  albumId: "album3-max-video10",
  qaSet: "mixed-image-video-487q",
  modelName: "qwen35-q4km",
};

function run(overrides: Partial<Run>): Run {
  return {
    run_id: "run-default",
    album_id: CONTEXT.albumId,
    qa_set: CONTEXT.qaSet,
    model_name: CONTEXT.modelName,
    graph_enabled: false,
    status: "completed",
    ...overrides,
  };
}

describe("filterGraphBaselineRuns", () => {
  it("只返回同配置下已完成的无图 run，并按完成时间倒序", () => {
    const result = filterGraphBaselineRuns(
      [
        run({ run_id: "older", finished_at: "2026-11-03T12:00:00+08:00" }),
        run({ run_id: "latest", finished_at: "2026-11-03T13:00:00+08:00" }),
        run({ run_id: "graph", graph_enabled: true }),
        run({ run_id: "wrong-album", album_id: "other" }),
        run({ run_id: "wrong-qa", qa_set: "compact-10q" }),
        run({ run_id: "wrong-model", model_name: "other-model" }),
        run({ run_id: "running", status: "running" }),
      ],
      CONTEXT,
    );

    expect(result.map((item) => item.run_id)).toEqual(["latest", "older"]);
  });
});
