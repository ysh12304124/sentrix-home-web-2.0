import { PROFILE_LABELS, OPEN_PROFILE } from '../lib/scoring.ts';
import type { PromptKindMeta } from '../hooks/useRunDetail.ts';
export interface ScoringPromptEditorProps {
  judgePromptKinds: PromptKindMeta[]; activePromptKind:string;
  onSwitchPromptKind:(kind:string)=>void; rejudgePrompt:string;
  onRejudgePromptChange:(value:string)=>void; judgeModel?:string;
  onSavePrompt?:()=>void; onResetPrompt:()=>void; running?:boolean;
}
export function ScoringPromptEditor({judgePromptKinds,activePromptKind,onSwitchPromptKind,rejudgePrompt,onRejudgePromptChange,judgeModel,onSavePrompt,onResetPrompt,running=false}:ScoringPromptEditorProps) {
  const group = activePromptKind.includes(':') ? activePromptKind.split(':')[0] : activePromptKind === 'answer_quality' ? OPEN_PROFILE : 'shared';
  const kinds = judgePromptKinds.filter(m => group === 'shared' ? ['task_decision','evidence'].includes(m.kind || '') : m.kind?.startsWith(group + ':'));
  const purposeNames: Record<string,string> = {answer_quality:'回答质量',choice:'选择题语义匹配',number:'数值匹配',open:'开放题判断',task_decision:'任务行为判断',evidence:'证据核验'};
  return (
      <details className="scoring-details scoring-prompt-editor">
        <summary>评分提示词 <span>{judgeModel || "未配置评分模型"}</span></summary>
        <div className="prompt-kind-tabs">
          <label>提示词所属标准 <select aria-label="提示词所属标准" value={group} onChange={e => {
            const selected = e.target.value;
            onSwitchPromptKind(selected === 'shared' ? 'task_decision' : selected + ':' + (selected === OPEN_PROFILE ? 'answer_quality' : 'choice'));
          }}><option value={OPEN_PROFILE}>{PROFILE_LABELS[OPEN_PROFILE]}</option><option value="atm_qs_v1">ATM-Bench式QS</option><option value="shared">公共诊断</option></select></label>
          <label>提示词用途 <select aria-label="提示词用途" value={activePromptKind} onChange={e => onSwitchPromptKind(e.target.value)}>
            {kinds.map(meta => <option key={meta.kind} value={meta.kind}>{purposeNames[(meta.kind || '').split(':').pop() || ''] || meta.kind}{meta.custom ? '（已自定义）' : ''}</option>)}
          </select></label>
        </div>
        {group === 'atm_qs_v1' && <p className="muted small">列表题固定使用 Jaccard＝|返回集合∩GT集合|÷|返回集合∪GT集合|，无需Judge提示词。单题详情可查看两个集合。</p>}
        <label className="rejudge-prompt">
          系统提示词（{purposeNames[activePromptKind.split(':').pop() || ''] || activePromptKind}）
          <textarea
            value={rejudgePrompt}
            disabled={running}
            rows={8}
            spellCheck={false}
            onChange={(event) => onRejudgePromptChange(event.target.value)}
          />
        </label>
        <div className="rejudge-toolbar">
          <span className="muted small">
            {rejudgePrompt.length} 字符 · Judge {judgeModel || "-"}
          </span>
          <div className="rejudge-actions">
            <button className="btn ghost compact" disabled={running} onClick={() => onSavePrompt?.()}>
              保存提示词
            </button>
            <button className="btn ghost compact" disabled={running} onClick={onResetPrompt}>
              恢复默认
            </button>

          </div>
        </div>
      </details>
  );
}
