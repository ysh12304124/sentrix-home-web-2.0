import { ATM_PROFILE, OPEN_PROFILE, PROFILE_LABELS, type ScoringSelection } from '../lib/scoring.ts';
export function ScoringSettings({value, onChange, disabled = false, hideGt = false}: {value: ScoringSelection; onChange: (v: ScoringSelection) => void; disabled?: boolean; hideGt?:boolean}) {
  return <fieldset className='scoring-standards' disabled={disabled}><legend>评分标准</legend>
    <p className='scoring-selection-hint'>可只选一套，也可同时选择两套；共用同一份答卷，分别计分。至少保留一套。</p>
    <div className='scoring-choices'>
    {Object.entries(PROFILE_LABELS).map(([id,label]) => <div key={id} className={'scoring-choice' + (value.profiles.includes(id) ? ' is-selected' : '')}>
      <label className='scoring-choice-title'><input aria-label={label} type='checkbox' checked={value.profiles.includes(id)} onChange={e => {
        const profiles = e.target.checked ? [...value.profiles,id] : value.profiles.filter(p => p !== id);
        if (profiles.length) onChange({...value,profiles});
      }} /><span>{label}</span><span className='scoring-choice-state' aria-hidden='true'>{value.profiles.includes(id) ? '已选择' : '未选择'}</span></label>
      <p className='scoring-choice-description'>{id === OPEN_PROFILE ? '所有题由Judge对照参考答案评0、1、2分。' : '选择与数值题匹配计0/1；列表Jaccard；开放题计0/1。'}</p>
      {!hideGt && <div className='scoring-gt-options'><label title='勾选后向Judge一并附加GT图片及每张图片对应的拍摄时间、可读地点。'>
        <input aria-label={label + ' · GT图片辅助'} type='checkbox' disabled={disabled || !value.profiles.includes(id)} checked={value.configs[id]?.use_gt_images ?? false}
          onChange={e => onChange({...value,configs:{...value.configs,[id]:{...value.configs[id], use_gt_images:e.target.checked}}})} />
        GT图片辅助
      </label><small>勾选后附加图片及对应的拍摄时间、可读地点。</small></div>}
    </div>)}
    </div>
    <details className='scoring-details'><summary>评分方式与补评说明</summary>
      <p>{PROFILE_LABELS[OPEN_PROFILE]}：所有题统一对照文字参考答案，由Judge评0/1/2；0为错误或未有效回答，1为部分正确，2为完整正确，保留历史行为规则。</p>
      <p>{PROFILE_LABELS[ATM_PROFILE]}：选择题由Judge逐项匹配，再核对答案键；不匹配任何选项归“其他”，得0分。数值题由Judge对照参考答案匹配，计0/1；开放题按ATM规则计0/1。列表题由程序计算Jaccard＝返回与GT的交集数量÷并集数量，真实空返回为0，缺记录或映射不完整则不可评分。</p>
      <p>两套标准复用同一份开放式答卷。QS按每道题指定的评估轮等权平均，中间轮仅作诊断。未评齐时只显示已评分均值与覆盖率；Judge失败不计0。模型媒体P/R/F1和工具媒体R@5单独保留，不进入QS。</p>
      <p>GT指标准答案及其材料。图片和元数据只辅助直接对照参考答案的Judge；选项映射不接收正确项标记，列表题固定按集合计算。勾选“GT图片辅助”后，每张GT图片与对应的拍摄时间、可读地点文字一起发送，按图片标识逐张对应；不勾选则不附加图片及这些拍摄资料。地点按冻结的媒体标注映射读取，不在评分时联网查询；缺失字段标为未记录。已启动批次沿用原配置和材料；读取已配置映射或图片失败会明确记录，图片开关不发送视频。</p>
      <p>补评只处理勾选标准中的指定范围，合法0分算已完成。同配置续写原批次；改提示词、Judge或GT配置会建立新批次，未覆盖题显示未采集，不拼接旧批次分数。比较时请同时核对题集、规则版本、Judge和GT配置；ATM-Bench式QS使用Judge匹配选择与数值题。</p>
    </details>
  </fieldset>;
}
