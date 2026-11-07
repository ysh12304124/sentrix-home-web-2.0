import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { it, expect, vi, afterEach } from "vitest";
import { CaseDiagnosis } from "./CaseDiagnosis.tsx";
afterEach(cleanup);
it("新诊断显示分类原因建议及独立依据入口", () => {
  const locate=vi.fn();
  render(<CaseDiagnosis diagnosis={{error_types:['unsupported_claim'],summary:'用表情推断主观感受',cause:'证据只能支持外观',certainty:'likely',next_step:'删除无依据推断',limitations:'内部决策原因未验证',evidence_refs:['i0:turn0']}} locate={locate}/>);
  expect(screen.getByLabelText('诊断结论').textContent).toContain('无依据推断');
  expect(screen.getByText(/AI 判断 · 原因推测/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'依据 1'}));expect(locate).toHaveBeenCalledWith('i0:turn0');
});
