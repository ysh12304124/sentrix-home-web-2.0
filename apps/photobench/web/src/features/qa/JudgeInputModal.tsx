/**
 * JUDGE 模型原始输入弹窗。移植 1.0 `App.vue:3796` 的
 * `<Teleport to="body">` + `judgeInput()`(`:719-723`) + `openJudgeInput/closeJudgeInput`(`:726-727`)。
 *
 * React 用 `createPortal(..., document.body)` 对应 `Teleport to="body"`：弹窗节点挂在
 * `body` 下、**不在组件树内**，所以 `.judge-modal-backdrop` 的 `position:fixed` 不会被
 * 任何祖先的 `transform` / `overflow` 影响（`photobench.css` 就是按 body 层级写的）。
 */
import { createPortal } from "react-dom";

import type { RunItemDetail } from "../../api/types.ts";

import { ItemScoringResults } from "../../components/ScoringResults.tsx";

type Dict = Record<string, unknown>;

const obj = (value: unknown): Dict => value && typeof value === "object" ? value as Dict : {};
const roles = [["judge", "回答评分"], ["task_judge", "回答行为评分"], ["evidence_judge", "证据评分"]] as const;

function judgeInputs(item: RunItemDetail) {
  const turns = item.runtime_turns || [];
  return roles.flatMap(([key, label]) => {
    const recorded = turns.flatMap((turn, i) => obj(turn[key]).input || Array.isArray(obj(turn[key]).attempts)
      ? [{ judge: obj(turn[key]), turn: i + 1 }] : []);
    const sources = recorded.length ? recorded : [{ judge: obj(item[key === "judge" ? "evaluation_result" : key] || item[key]), turn: null }];
    return sources.flatMap(({ judge, turn }) => {
      const attempts = Array.isArray(judge.attempts) ? judge.attempts.map(obj) : [];
      const inputs = attempts.filter(a => Array.isArray(obj(a.input).messages));
      if (!inputs.length && Array.isArray(obj(judge.input).messages)) inputs.push({ input: judge.input });
      return inputs.map((attempt, index) => ({
        title: `${turn == null ? "" : `第 ${turn} 轮 · `}${label} · 请求 ${index + 1}${attempt.purpose ? ` · ${String(attempt.purpose)}` : ""}`,
        input: attempt.input,
        response: attempt.raw_response,
        error: attempt.error,
      }));
    });
  });
}

export interface JudgeInputModalProps {
  item: RunItemDetail;
  onClose: () => void;
}

export function JudgeInputModal({ item, onClose }: JudgeInputModalProps) {
  const inputs = judgeInputs(item);
  const scorer = String(obj(item.evaluation_result || item.judge).scorer || "");
  const deterministic = ["media_set", "atm_list", "atm_number"].includes(scorer);
  return createPortal(
    <div
      className="judge-modal-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="judge-modal" role="dialog" aria-modal="true" aria-label="Judge 模型原始输入">
        <header>
          <div>
            <h3>JUDGE 模型原始输入</h3>
            <span className="muted small">{item.qa_id ?? ""}</span>
          </div>
          <button className="judge-modal-close" type="button" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="judge-requests">
        {(item.scoring_results as unknown[] | undefined)?.length ? <ItemScoringResults results={item.scoring_results} diagnostics={item.diagnostic_results} /> : null}
        {inputs.length ? inputs.map((entry, index) => (
          <section className="judge-request" key={index}>
            <h4>{entry.title}</h4>
            <pre>{JSON.stringify(entry.input, null, 2)}</pre>
            {entry.response != null && <details><summary>Judge 原始返回</summary><pre>{JSON.stringify(entry.response, null, 2)}</pre></details>}
            {entry.error != null && <p className="judge-input-note">请求失败：{String(entry.error)}</p>}
          </section>
        )) : (item.scoring_results as unknown[] | undefined)?.length ? null : (
          <p className="judge-input-note">{deterministic ? "本题主分由程序按确定性规则计算；当前记录中没有 Judge 模型请求。" : "该历史结果在运行时未保存 Judge 原始请求 JSON，无法恢复。"}</p>
        )}
        </div>
      </section>
    </div>,
    document.body,
  );
}
