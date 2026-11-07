import { PROFILE_LABELS, OPEN_PROFILE } from '../lib/scoring.ts';
import { EvaluationResult } from './EvaluationResult.tsx';
import { ScoringRequestLog } from './ScoringRequestLog.tsx';
type Dict = Record<string, unknown>;
const obj = (v: unknown): Dict => v && typeof v === 'object' ? v as Dict : {};
const percent = (v:unknown) => typeof v === 'number' ? (v*100).toFixed(1)+'%' : '未完成';
export function ScoringResults({summary}: {summary:unknown}) {
  const s = obj(summary), scoring = obj(s.scoring), profiles = obj(scoring.profiles);
  return <section className='scoring-results' aria-label='两套评分结果'><h3>评分结果</h3><div className='scoring-result-grid'>
    {Object.entries(PROFILE_LABELS).map(([id,label]) => {const result=obj(profiles[id]), gt=obj(result.gt_config), judge=obj(result.judge);
      const collected = Boolean(result.status && result.status !== 'not_collected');
      const complete = typeof result.total_score === 'number';
      const value = complete ? result.total_score : result.normalized_mean;
      const isOpen = id === OPEN_PROFILE;
      const displayValue = isOpen
        ? typeof result.scored_mean === 'number' ? result.scored_mean.toFixed(2) + ' / 2.00 分' : '—'
        : typeof value === 'number' ? percent(value) : '—';
      const valueLabel = isOpen
        ? complete ? '回答质量均分' : '已评分回答质量均分'
        : complete ? 'QS总分' : '已评分QS均值';
      return <article className='scoring-metric-card' key={id} aria-label={label}>
        <header className='scoring-result-head'><b>{label}</b><span className={'phase-status ' + (complete ? 'completed' : collected ? 'partial' : '')}>{!collected ? '未运行采集' : complete ? '已完成' : '未完成'}</span></header>
        {!collected ? <div className='scoring-result-empty'>选择该标准并补评后，将显示得分与覆盖率。</div> : <>
          <div className='scoring-value-row'><strong>{displayValue}</strong><span>{valueLabel}</span></div>
          <div className='scoring-coverage'><span>覆盖率 {String(result.scored)}/{String(result.total)} · {percent(result.coverage)}</span><progress aria-label={label+'评分覆盖率'} max={1} value={typeof result.coverage === 'number' ? result.coverage : 0} /></div>
          {Number(result.diagnostic_pending) > 0 && <p className='scoring-pending'>公共诊断待完成 {String(result.diagnostic_pending)} 项，评分覆盖率单独计算。</p>}
          <details className='scoring-result-details'><summary>评分版本与配置</summary><dl>
            <div><dt>题集</dt><dd>{String(result.qa_set || '-')}</dd></div>
            <div><dt>规则版本</dt><dd>{String(result.rule_version || '-')}</dd></div>
            <div><dt>Judge</dt><dd>{String(judge.model || '-')}</dd></div>
            <div><dt>GT辅助</dt><dd>{gt.gt_metadata_mode === 'image_metadata_v1' ? gt.use_gt_images ? '图片＋对应拍摄时间与可读地点' : '关闭（图片及拍摄资料均不附加）' : <>图片{gt.use_gt_images ? '开启' : '关闭'} · {gt.gt_metadata_mode === 'readable_v1' ? '拍摄时间与可读地点（历史文字协议）' : '历史元数据' + (gt.use_gt_metadata ? '开启' : '关闭')}</>}</dd></div>
            {Boolean(gt.use_gt_metadata) && ['readable_v1','image_metadata_v1'].includes(String(gt.gt_metadata_mode)) && <div className='scoring-config-wide'><dt>地点标注</dt><dd>{String(gt.annotation_version || '未记录')} · <code>{String(gt.mapping_sha256 || '无映射记录')}</code></dd></div>}
            <div><dt>实际Judge请求</dt><dd>{String(result.judge_calls || 0)} 次</dd></div>
            <div className='scoring-config-wide'><dt>批次</dt><dd><code>{String(result.batch_id)}</code></dd></div>
            <div className='scoring-config-wide'><dt>QA快照</dt><dd><code>{String(result.qa_definition_sha256 || '-')}</code></dd></div>
          </dl></details>
        </>}
      </article>;
    })}
  </div><p className='scoring-result-note'>开放式回答由Judge按0/1/2评分，展示原始均分，满分2.00分。ATM-Bench式QS按题等权平均，以百分比展示；选择与数值题使用Judge匹配。未评齐时均值仅包含已评分题目，覆盖率表示评分完成比例，完整总分需覆盖全部题目。媒体召回率在公共诊断指标中单独展示。跨测评比较需核对题集、规则版本、Judge和GT配置。</p></section>;
}
export function ItemScoringResults({results, diagnostics, current, definition}: {results:unknown; diagnostics?:unknown; current?:unknown; definition?:unknown}) {
  const rows = Array.isArray(results) ? results.map(obj) : [];
  return <section className='item-scoring-results' aria-label='分标准评分记录'>
    <header className='item-scoring-heading'><h4>分标准评分记录</h4><span>同一回答 · 独立计分</span></header>
    <div className='item-scoring-grid'>
      {Object.entries(PROFILE_LABELS).map(([id,label]) => {
        const selected = rows.filter(r => r.profile_id === id);
        const latest = obj(obj(current)[id]);
        const isOpen = id === OPEN_PROFILE;
        return <article className={'item-score-card ' + (isOpen ? 'is-open-score' : 'is-qs-score')} key={id} aria-label={label}>
          <header className='item-score-card-head'><span className='item-score-mark' aria-hidden='true'>{isOpen ? '01' : 'QS'}</span><div><h5>{isOpen ? '开放式回答' : 'ATM-Bench式QS'}</h5><p>{isOpen ? 'Judge · 0 / 1 / 2 分' : '按题型规则评分'}</p></div></header>
          {Object.keys(latest).length ? latest.status === 'not_collected' ? <p className='item-score-empty'>未运行采集</p> : <EvaluationResult value={latest} definition={definition} /> : !selected.length ? <p className='item-score-empty'>未运行采集</p> : null}
          {!!selected.length && <details className='score-history'><summary>评分调用与历史记录（{selected.length}条）</summary>{selected.map(r => {
            const scored = typeof r.score === 'number';
            const status = scored ? '已评分' : r.status === 'failed' || r.status === 'error' ? '评分失败' : r.status === 'running' ? '评分中' : '待评分';
            return <details className='item-score-record' key={String(r.key)}>
              <summary><span className='item-score-record-main'><strong>{scored ? String(r.score) : '—'}{scored && <small> / {String(r.max_score)} 分</small>}</strong><span className={'item-score-status ' + (scored ? 'is-scored' : 'is-pending')}>{status}</span></span><span className='item-score-record-meta'><span>第{Number(r.turn_index)+1}轮 · {r.is_evaluation_turn ? '计入总分' : '诊断轮'}</span><span className='item-score-expand'>评分详情 <i aria-hidden='true'>⌄</i></span></span></summary>
              <div className='item-score-record-body'><div className='item-score-batch'>评分批次 <code>{String(r.batch_id)}</code></div>{r.reason != null && <p className='item-score-reason'>{String(r.reason)}</p>}{r.formula != null && <p className='item-score-formula'>{String(r.formula)} · GT {JSON.stringify(r.target)} · 返回 {JSON.stringify(r.predicted)}</p>}<ScoringRequestLog requests={[...(Array.isArray(r.history) ? r.history : []).flatMap(h => Array.isArray(obj(h).requests) ? obj(h).requests as unknown[] : []),...(Array.isArray(r.requests) ? r.requests : [])]} /></div>
            </details>;
          })}</details>}
        </article>;
      })}
    </div>
    <div className='item-scoring-diagnostics'><ScoringRequestLog requests={diagnostics} title='公共诊断记录（相同配置复用）' /></div>
  </section>;
}
