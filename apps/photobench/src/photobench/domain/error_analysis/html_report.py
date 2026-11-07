"""Self-contained report layout and offline case reader; evaluation facts stay unchanged."""

from __future__ import annotations

import html
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from photobench.domain.error_analysis.html_dashboard import render_charts, score_composition, selected_turns
from photobench.domain.evaluation.presentation import evaluation_summary_html

STYLES = """
:root{color-scheme:light;--ink:#1c2c43;--muted:#62738a;--line:#dfe6ef;
--green:#248278;--amber:#ba791c;--red:#bb5360;--gray:#8996a8;--blue:#315b92}
*{box-sizing:border-box}html{scroll-padding-top:76px}body{margin:0;background:#f2f5fa;
color:var(--ink);font:15px/1.65 system-ui,-apple-system,"PingFang SC",sans-serif}
main{max-width:1280px;margin:auto;padding:30px 28px 64px}h1,h2,h3,p{margin:0 0 14px}
h1{font-size:32px;line-height:1.35}h2{font-size:22px}h3{font-size:16px}
a{color:var(--blue)}header{background:#1b304d;color:#fff;border-radius:20px;padding:32px}
header p{color:#cad7e8}header .eyebrow{letter-spacing:2px;font-size:12px;color:#abc5e4}
.meta{font-size:13px;overflow-wrap:anywhere}.muted,.note{color:var(--muted);font-size:13px}
nav{display:flex;gap:8px;flex-wrap:wrap;position:sticky;top:0;z-index:5;background:#f2f5faf5;
padding:12px 0}nav a{padding:7px 15px;border-radius:8px;background:white;text-decoration:none;
border:1px solid var(--line);font-size:14px}.panel{padding:26px;background:white;border:1px solid
var(--line);border-radius:16px;margin:18px 0;min-width:0}section{scroll-margin-top:10px}
.kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:20px 0}
.kpi{border:1px solid var(--line);border-radius:14px;background:white;padding:20px}
.kpi strong{display:block;font-size:30px;line-height:1.6;color:var(--green)}
.kpi span{font-size:13px;color:var(--muted)}.grid{display:grid;grid-template-columns:
repeat(2,minmax(0,1fr));gap:20px}.grid>*{min-width:0}.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:560px;font-size:13px;margin:12px 0}th,td{text-align:left;
padding:11px 10px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}
th{background:#f3f6fa;color:#52647d;font-weight:600;white-space:nowrap}td:first-child{min-width:100px}
.numeric{font-variant-numeric:tabular-nums}.stack{display:flex;height:12px;overflow:hidden;
border-radius:6px;background:#eef1f6;min-width:90px}.stack span{display:block;min-width:0}
.s2{background:var(--green)}.s1{background:var(--amber)}.s0{background:var(--red)}
.sNone{background:var(--gray)}.legend{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0}
.legend span{font-size:13px}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;
margin-right:6px}.badge{display:inline-block;padding:3px 9px;border-radius:6px;
background:#edf2f8;color:#50637d;font-size:12px;font-weight:500;white-space:normal}
.badge.s2{background:#e7f3f0;color:#216d63}.badge.s1{background:#fff3db;color:#865712}
.badge.s0{background:#fbecef;color:#9c3f50}.badge.sNone{background:#eef1f5;color:#627087}
.notice{border-left:3px solid #dca148;background:#fff8eb;padding:14px 18px;margin:14px 0}
details{border:1px solid var(--line);border-radius:9px;margin:12px 0;background:white}
summary{cursor:pointer;padding:13px 16px;font-weight:600;overflow-wrap:anywhere}
summary:hover{background:#f6f8fc}details[open]>summary{border-bottom:1px solid var(--line)}
.detail-body{padding:16px}.detail-body>:last-child{margin-bottom:0}pre,.text{white-space:pre-wrap;
overflow-wrap:anywhere;word-break:break-word}pre{background:#f4f6fa;padding:14px;border-radius:8px;
font:12px/1.65 ui-monospace,monospace;max-height:440px;overflow:auto;margin:0}
.case-card{margin:10px 0}.case-card>summary{list-style:none;display:grid;
grid-template-columns:135px minmax(0,1fr) 20px;gap:16px;align-items:start;padding:18px}
.case-card>summary::-webkit-details-marker{display:none}.case-card>summary:after{content:'＋';
color:var(--muted)}.case-card[open]>summary:after{content:'−'}.case-id{font-size:12px;
font-weight:500;display:block;color:var(--muted);margin-top:5px}.case-question{font-size:15px}
.case-meta{display:block;font-size:12px;color:var(--muted);font-weight:400;margin-top:5px}
.turn{background:#f7f9fc;border:1px solid var(--line);border-radius:10px;padding:18px;margin:14px 0}
.turn-heading{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.turn-heading h3{margin:0}.answer-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
gap:14px;margin-top:14px}.answer-box{padding:16px;background:white;border:1px solid var(--line);
border-radius:8px;min-width:0}.answer-box h4{font-size:13px;color:var(--muted);margin:0 0 10px}
.answer-box .text{font-size:14px}.turn details{margin-bottom:0}.turn details summary{font-size:13px}
.controls{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0;align-items:end}
label{font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:5px}
label.search{flex:1;min-width:220px}input,select,button{font:inherit;border:1px solid #ccd7e5;
border-radius:7px;padding:9px 12px;background:white;color:var(--ink);min-height:42px}
input{width:100%;min-width:0}button{cursor:pointer}button:disabled{opacity:.45;cursor:default}
button:hover:not(:disabled){background:#edf3fa}button:focus-visible,summary:focus-visible,
input:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid #8cb5e5;outline-offset:2px}
.behavior-matrix{border-collapse:separate;border-spacing:6px;table-layout:fixed;min-width:680px}
.behavior-matrix th{background:none;white-space:normal;text-align:center;border:0;vertical-align:middle}
.behavior-matrix th:first-child{width:150px;text-align:left}.behavior-matrix td{padding:0;border:0}
.behavior-matrix .heat-cell{width:100%;min-height:90px;border-radius:9px;border:1px solid #dce6e5;
background:var(--heat);color:#203649;text-align:center;padding:12px 6px}
.heat-cell strong{font-size:23px;display:block;font-variant-numeric:tabular-nums;line-height:1.4}
.heat-cell span{font-size:12px}.behavior-matrix .heat-cell:hover:not(:disabled){background:var(--heat);
box-shadow:inset 0 0 0 2px #526985}.heat-cell[aria-pressed=true]{outline:3px solid var(--blue)}
.behavior-matrix .heat-cell:disabled{opacity:1;color:#8a96a5;border-color:#edf0f4}
.behavior-matrix caption{text-align:left;font-size:13px;color:var(--muted)}
.behavior-matrix th small{display:block;color:var(--muted);font-weight:400}
.heat-legend{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:14px}
.heat-legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:5px}
.pagination{display:flex;gap:12px;align-items:center;justify-content:space-between;margin-top:18px}
.empty{padding:28px;text-align:center;border:1px dashed var(--line);border-radius:10px}
.section-heading{display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin-top:30px}
.section-heading p{max-width:680px}.chart-card{border:1px solid var(--line);border-radius:14px;
background:white;padding:24px;min-width:0}.chart-card h3{font-size:18px;letter-spacing:-.3px}
.chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin:18px 0}
.chart-card .note{line-height:1.7}.chart-card small{font-variant-numeric:tabular-nums}
.donut-layout{display:flex;align-items:center;gap:28px;min-height:215px}
.donut{width:180px;height:180px;flex:0 0 180px;border-radius:50%;padding:22px}
.donut>div{background:white;border-radius:50%;height:100%;display:flex;flex-direction:column;
align-items:center;justify-content:center}.donut strong{font-size:32px;font-variant-numeric:tabular-nums}
.donut small{font-size:12px;color:var(--muted)}.chart-legend{list-style:none;padding:0;margin:0;flex:1}
.chart-legend li{display:grid;grid-template-columns:8px minmax(0,1fr) auto auto;gap:9px;align-items:center;
padding:10px 0;border-bottom:1px solid #edf0f5;font-size:13px}.chart-legend i{width:8px;height:8px;
border-radius:50%}.chart-legend small{color:var(--muted);min-width:44px;text-align:right}
.chart-link{border:0;padding:0;min-height:28px;background:none;color:var(--blue);font-size:13px;
text-align:left;line-height:1.5;overflow-wrap:anywhere}.chart-link:hover:not(:disabled){background:#edf3fa}
.chart-columns,.quality-row{display:grid;grid-template-columns:minmax(100px,1.25fr) minmax(70px,1fr) 58px;
gap:14px;align-items:center}.chart-columns{font-size:11px;color:var(--muted);margin:20px 0 5px}
.quality-row{padding:11px 0;border-bottom:1px solid #eef1f5}.quality-row small,.scenario-row small{
display:block;font-size:11px;color:var(--muted);margin-top:4px}.quality-row strong{text-align:right;
font-size:14px;font-variant-numeric:tabular-nums}.score-track{display:flex;border-radius:4px;
overflow:hidden;height:18px;background:#f0f3f7}.score-track span{display:block;min-width:0}
.metric-bar{display:grid;grid-template-columns:1fr auto;gap:8px;padding:15px 0}
.metric-bar>div{grid-column:1/-1;height:12px;background:#edf2f7;border-radius:6px;overflow:hidden}
.metric-bar i{display:block;height:100%;background:var(--green);border-radius:inherit}
.metric-bar b{font-size:24px;line-height:1.1}.metric-bar small{grid-column:1/-1;color:var(--muted)}
.retrieval-matrix{display:table;width:100%;border-spacing:5px;margin:16px 0;table-layout:fixed}
.matrix-row{display:table-row}.matrix-row>*{display:table-cell;vertical-align:middle;text-align:center}
.matrix-row>[role=columnheader],.matrix-row>[role=rowheader]{font-size:12px;color:var(--muted)}
.matrix-row>:first-child{width:58px}.matrix-row>[role=columnheader]{padding-bottom:8px}
.matrix-cell{border-radius:8px;padding:16px 5px;font-variant-numeric:tabular-nums}
.matrix-cell span,.matrix-cell small{display:block;font-size:12px}
.matrix-cell strong{display:block;font-size:27px;margin:4px 0}.matrix-cell small{opacity:.8}
.matrix-cell.hit.correct{background:#e7682f;color:white}.matrix-cell.hit.wrong{background:#f9d6c6;color:#703c25}
.matrix-cell.miss.correct{background:#6d787f;color:white}.matrix-cell.miss.wrong{background:#e5e8eb;color:#3c4854}
.matrix-conditions{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding-top:10px}
.matrix-conditions>div{background:#f5f7fa;border-radius:8px;padding:12px}
.matrix-conditions span,.matrix-conditions small{display:block;font-size:12px;color:var(--muted)}
.matrix-conditions b{display:block;font-size:21px;margin:4px 0}
.transition-grid{display:grid;grid-template-columns:1.7fr repeat(3,1fr);gap:6px;text-align:center;
align-items:center;margin:20px 0}.transition-grid>span:not(.note){padding:13px 5px;border-radius:6px;
font-size:20px;font-variant-numeric:tabular-nums}.transition-grid b{font-size:12px}
.scenario-row{display:flex;justify-content:space-between;align-items:center;padding:17px 0;
border-bottom:1px solid var(--line)}.scenario-row strong{color:var(--green);font-size:25px}
.signal-row{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);
padding:12px 0;font-size:13px}.signal-row b{font-variant-numeric:tabular-nums}.scope-note{padding:10px 14px;
background:#edf4fa;border-radius:7px;color:var(--blue)}.range-control{max-width:100px}

.score-tabs{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}
#score-composition .chart-grid{align-items:start}
.score-tabs [aria-selected=true]{background:var(--blue);color:white}
.composition-row{padding:12px 0;border-bottom:1px solid var(--line)}
.composition-row>div:first-child{display:flex;justify-content:space-between;gap:12px;align-items:center}
.composition-row span{font-size:13px;white-space:nowrap}
.composition-track{height:12px;background:#edf2f7;border-radius:4px;margin-top:7px;overflow:hidden}
.composition-track i{display:block;height:100%;background:var(--blue)}
.review-heading{margin-top:28px!important;border-top:1px solid var(--line);padding-top:22px}
.reader-shell{border:0;margin:0}.reader-shell>summary{padding:0;list-style:none;display:flex;
justify-content:space-between;align-items:center;gap:16px}.reader-shell>summary::-webkit-details-marker{
display:none}.reader-shell>summary:after{content:'展开全部样例 ＋';font-size:13px;color:var(--blue);
white-space:nowrap}.reader-shell[open]>summary:after{content:'收起全部样例 −'}
.reader-shell[open]>summary{padding-bottom:18px}.reader-shell>summary h2{margin:0}
.report-legend{font-size:12px;display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px;color:var(--muted)}
.report-legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px}
[hidden]{display:none!important}.footer{padding:14px 0;color:var(--muted);font-size:12px}
@media(min-width:1000px){.distribution th:nth-child(3){min-width:170px}}
@media(max-width:800px){main{padding:16px}.grid{grid-template-columns:1fr}.kpis{gap:10px}
.kpi{padding:15px}.kpi strong{font-size:25px}header{padding:26px}h1{font-size:27px}
.panel{padding:18px}.answer-grid{grid-template-columns:1fr}}
@media(max-width:1000px){.donut-layout{flex-direction:column;gap:10px}.chart-legend{width:100%}}
@media(max-width:700px){.chart-grid{grid-template-columns:1fr}.chart-card{padding:18px}
.donut-layout{flex-direction:row;gap:18px}.donut{width:130px;height:130px;flex-basis:130px;padding:16px}
.donut strong{font-size:24px}.section-heading{display:block}.chart-columns,.quality-row{gap:9px;
grid-template-columns:minmax(95px,1.2fr) minmax(55px,1fr) 52px}.reader-shell>summary{align-items:flex-start}
.reader-shell>summary h2{font-size:19px}}
@media(max-width:380px){.donut-layout{flex-direction:column}.reader-shell>summary{flex-wrap:wrap}}
@media(max-width:520px){html{scroll-padding-top:120px}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
.case-card>summary{grid-template-columns:minmax(0,1fr) 20px;gap:8px;padding:14px}
.case-card>summary>.case-identity{grid-column:1/-1}.case-id{display:inline;margin-left:7px}
nav a{padding:6px 10px}.turn{padding:12px}.controls label{flex:1;min-width:130px}
.controls label.search{min-width:100%}select{max-width:100%}.pagination{flex-wrap:wrap}}
@media print{body,nav{background:white}main{max-width:none;padding:0}header{background:white;
color:var(--ink);border-bottom:2px solid var(--ink);border-radius:0}header p{color:var(--muted)}
nav,.controls,.pagination,.no-print{display:none!important}.panel,.kpi{box-shadow:none;
break-inside:avoid}.case-card{break-inside:avoid}.scroll,pre{overflow:visible;max-height:none}
.answer-grid{grid-template-columns:1fr 1fr}}
"""

CONTROLLER = r"""
(()=>{
  const data=JSON.parse(document.getElementById('report-data').textContent);
  const {cases, labels, scores, content_unit}=data;
  const $ = id => document.getElementById(id);
  const cards = [...document.querySelectorAll('.case-card')];
  const size = 25;
  let page = 0, filtered = cases.map((_,i)=>i);
  const show = value => value === null || value === undefined || value === '' ? '未记录' : String(value);
  const label = value => labels[value] || show(value);
  const scoreKey = value => value === null || value === undefined ? 'None' : String(value);
  const node = (tag, text, className) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = show(text);
    if (className) n.className = className;
    return n;
  };
  const badge = (text, className='') => node('span', text, 'badge '+className);
  function fold(title, content) {
    const d=node('details'); d.append(node('summary', title));
    const b=node('div',undefined,'detail-body'); b.append(content);d.append(b);return d;
  }
  function paragraph(text) {
    const p=node('div', text, 'text');
    return String(text || '').length > 1400 ? fold('展开全文（'+String(text).length+'字）', p) : p;
  }
  function behavior(t) {
    return t.behavior_correct === true ? '符合预期' : t.behavior_correct === false ? '不符合预期' : '未判定';
  }
  function table(headers, rows) {
    const wrap=node('div',undefined,'scroll'), t=node('table'), head=node('thead'), hr=node('tr');
    headers.forEach(h=>hr.append(node('th',h)));head.append(hr);t.append(head);
    const body=node('tbody');rows.forEach(row=>{const tr=node('tr');
      row.forEach(v=>tr.append(node('td',v)));body.append(tr);});t.append(body);wrap.append(t);return wrap;
  }
  function hydrate(i) {
    const card=cards[i];if(card.dataset.loaded)return;
    const c=cases[i], b=node('div',undefined,'detail-body');
    if(c.scoring_evidence){
      b.append(node('h3','题型评分与扣分依据 · '+label(c.scoring_evidence.question_type)),
        fold('评分标准、原分、判分理由及列表差异',node('pre',JSON.stringify(c.scoring_evidence,null,2))));
    }
    if(c.text_truncated)b.append(node('p','分析摘要曾截短；完整文本与调用轨迹请从运行页导出。','notice'));
    const turns=c.turns.length ? c.turns : [{question:c.question,answer:c.answer,
      reference_answer:c.reference_answer,score:c.score,turn:0}];
    if(turns.length>1)b.append(table(['轮次','原质量分','预期行为','实际行为','行为判定'],turns.map(t=>[
      '第'+(t.turn+1)+'轮',scores[scoreKey(t.score)],label(t.expected),label(t.actual),behavior(t)])));
    if(turns.some(t=>t.expected==='clarify')) b.append(node('p',
      '澄清轮原质量分评价本轮任务要求。首轮直接回答与后轮目标答案的内容分需要独立复核，本导出仅展示已保存的原分。','note'));
    for(const t of turns){
      const box=node('section',undefined,'turn'), heading=node('div',undefined,'turn-heading');
      heading.append(node('h3','第'+(t.turn+1)+'轮'),badge(scores[scoreKey(t.score)],'s'+scoreKey(t.score)));
      if(c.matched_turns && !c.matched_turns.includes(t.turn)) heading.append(badge('上下文轮'));
      const actionText='预期：'+label(t.expected)+' → 实际：'+label(t.actual)+' · '+behavior(t);
      box.append(heading,node('p',actionText,'note'),
        node('h3','用户问题'),paragraph(t.question));
      const answers=node('div',undefined,'answer-grid');
      for(const [title,text] of [['模型回答',t.answer],['本轮参考答案（GT）',t.reference_answer]]){
        const a=node('div',undefined,'answer-box');
        a.append(node('h4',title),paragraph(text));answers.append(a);
      }
      box.append(answers);
      if(t.judge_reason)box.append(fold('行为判定依据',paragraph(t.judge_reason)));
      const meta={...t};delete meta.question;delete meta.answer;delete meta.reference_answer;
      box.append(fold('本轮标签与保存记录',node('pre',JSON.stringify(meta,null,2))));b.append(box);
    }
    if(c.events.length){
      const events=node('div');
      c.events.forEach(e=>events.append(fold('第'+(e.turn+1)+'轮 · '+label(e.label)+' · '+label(e.recovery),
        node('pre',JSON.stringify(e,null,2)))));
      b.append(fold('运行异常记录（'+c.events.length+'次）',events));
    }
    b.append(fold('案例配置与原始分析记录',node('pre',JSON.stringify(c,null,2))));
    card.append(b);card.dataset.loaded='1';
  }
  cards.forEach((c,i)=>c.addEventListener('toggle',()=>{if(c.open)hydrate(i);}));
  // Search across every saved turn, including follow-up questions and references.
  const searches=cases.map(c=>[c.qa_id,c.question,c.answer,c.reference_answer,
    ...c.turns.flatMap(t=>[t.question,t.answer,t.reference_answer])].join(' ').toLocaleLowerCase());
  const options = (id, values) => [...new Set(values)].filter(Boolean).forEach(v=>{
    const o=node('option',label(v));o.value=v;$(id).append(o);
  });
  options('reader-ability',cases.flatMap(c=>(c.dimension_labels || {}).ability_family || []));
  options('reader-execution',cases.map(c=>c.execution));
  const turnsInScope=c=>c.turns.filter(t=>!c.matched_turns || c.matched_turns.includes(t.turn));
  const values=(c,dim)=>content_unit==='turn' ? turnsInScope(c).flatMap(t=>t[dim] || []) :
    (c.dimension_labels || {})[dim] || [];
  function categoryOptions(){
    $('reader-category').replaceChildren(new Option('全部类别',''));
    const dim=$('reader-dimension').value;
    if(dim)options('reader-category',cases.flatMap(c=>values(c,dim)));
  }
  function render(){
    const pages=Math.max(1,Math.ceil(filtered.length/size));page=Math.max(0,Math.min(page,pages-1));
    cards.forEach(c=>{c.hidden=true;});
    filtered.slice(page*size,(page+1)*size).forEach(i=>{cards[i].hidden=false;});
    $('reader-count').textContent='当前明细 '+filtered.length+' / '+cases.length+' 题 · 每页25题';
    $('reader-page').textContent=(page+1)+' / '+pages;
    $('reader-prev').disabled=page===0;$('reader-next').disabled=page===pages-1;
    $('reader-empty').hidden=filtered.length>0;
  }
  function apply(){
    const q=$('reader-search').value.trim().toLocaleLowerCase(), s=$('reader-score').value,
      a=$('reader-ability').value,x=$('reader-execution').value,b=$('reader-behavior').value,
      expected=$('reader-expected').value,actual=$('reader-actual').value,
      dim=$('reader-dimension').value,category=$('reader-category').value,
      from=Number($('reader-from').value || 1),to=Number($('reader-to').value || cases.length);
    filtered=cases.flatMap((c,i)=>{
      const matched=(!q || searches[i].includes(q)) &&
        (!s || content_unit==='turn' || scoreKey(c.score)===s) &&
        (!a || ((c.dimension_labels || {}).ability_family || []).includes(a)) && (!x || c.execution===x) &&
        (i+1>=from && i+1<=to) &&
        (!category || content_unit==='turn' || values(c,dim).includes(category)) &&
        ((!b && !expected && !actual && !(category && content_unit==='turn') &&
          !(s && content_unit==='turn')) || c.turns.some(t=>
          (!c.matched_turns || c.matched_turns.includes(t.turn)) &&
          (!category || content_unit!=='turn' || (t[dim] || []).includes(category)) &&
          (!s || content_unit!=='turn' || scoreKey(t.score)===s) &&
          (!b || (t.expected==='clarify' && t.actual==='answer')) &&
          (!expected || (t.expected || 'unknown')===expected) &&
          (!actual || (t.actual || 'unknown')===actual)));
      return matched?[i]:[];
    });page=0;render();
    const scope=$('reader-scope');scope.hidden=!(category || s || x || expected || actual);
    scope.textContent='当前筛选：'+[category ? label(dim)+' / '+label(category) : '',
      s ? scores[s] : '',x ? label(x) : '',expected ? '预期'+label(expected) : '',
      actual ? '实际'+label(actual) : ''].filter(Boolean).join(' · ');
    document.querySelectorAll('.heat-cell').forEach(cell=>cell.setAttribute('aria-pressed',
      String(!!expected && !!actual && cell.dataset.expected===expected && cell.dataset.actual===actual)));
  }
  for(const id of ['reader-search','reader-score','reader-ability','reader-execution','reader-behavior',
    'reader-expected','reader-actual','reader-category','reader-from','reader-to'])
    $(id).addEventListener('input',apply);
  $('reader-dimension').addEventListener('input',()=>{categoryOptions();apply();});
  $('reader-reset').onclick=()=>{for(const e of document.querySelectorAll('.controls input,.controls select'))
    e.value='';categoryOptions();apply();};
  for(const [id,key] of [['reader-expected','expected'],['reader-actual','actual']])
    options(id,cases.flatMap(c=>c.turns.map(t=>t[key] || 'unknown')));
  document.querySelectorAll('.heat-cell').forEach(cell=>cell.addEventListener('click',()=>{
    $('reader-reset').click();$('reader-expected').value=cell.dataset.expected;
    $('reader-actual').value=cell.dataset.actual;apply();$('case-reader').open=true;
    $('cases').scrollIntoView({block:'start'});
  }));
  document.querySelectorAll('[data-composition-tab]').forEach(tab=>tab.addEventListener('click',()=>{
    document.querySelectorAll('[data-composition-tab]').forEach(t=>t.setAttribute('aria-selected',String(t===tab)));
    document.querySelectorAll('[data-composition-score]').forEach(p=>p.hidden=p.dataset.compositionScore!==tab.dataset.compositionTab);
  }));
  document.querySelectorAll('.chart-link[data-dimension]').forEach(link=>link.addEventListener('click',()=>{
    $('reader-reset').click();const dim=link.dataset.dimension,value=link.dataset.value;
    if(dim==='score')$('reader-score').value=value;
    else if(dim==='execution')$('reader-execution').value=value;
    else{$('reader-dimension').value=dim;categoryOptions();$('reader-category').value=value;}
    const scorePanel=link.closest('[data-composition-score]');
    if(scorePanel)$('reader-score').value=scorePanel.dataset.compositionScore;
    apply();$('case-reader').open=true;$('cases').scrollIntoView({block:'start'});
  }));
  document.querySelectorAll('nav a[href="#cases"]').forEach(a=>a.addEventListener('click',()=>{
    $('case-reader').open=true;
  }));
  $('reader-prev').onclick=()=>{page--;render();};$('reader-next').onclick=()=>{page++;render();};
  $('reader-open').onclick=()=>filtered.slice(page*size,(page+1)*size).forEach(i=>{hydrate(i);cards[i].open=true;});
  $('reader-close').onclick=()=>cards.forEach(c=>{c.open=false;});
  // Expand details for printing, then restore the reader state.
  let printState=[];
  window.addEventListener('beforeprint',()=>{printState=[...document.querySelectorAll('details[open]')];
    // Printing respects the current compact report and sample fold state.
    if($('case-reader').open)filtered.slice(page*size,(page+1)*size).forEach(i=>{hydrate(i);cards[i].open=true;});});
  window.addEventListener('afterprint',()=>{document.querySelectorAll('details[open]').forEach(d=>{d.open=false;});
    printState.forEach(d=>{d.open=true;});});
  $('reader-tools').hidden=false;render();
})();
"""


def escape(value: Any) -> str:
    return html.escape(str(value if value is not None else "未记录"), quote=True)


def document(body: str, *, controller: bool = False) -> str:
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>PhotoBench 错误分析</title><style>{STYLES}</style></head><body><main>"
        + body
        + "</main>"
        + (f"<script data-report-controller>{CONTROLLER}</script>" if controller else "")
        + "</body></html>"
    )


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    return (
        '<div class="scroll"><table><thead><tr>'
        + "".join(f"<th>{escape(h)}</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(f"<td>{escape(v)}</td>" for v in row) + "</tr>" for row in rows)
        + "</tbody></table></div>"
    )


def fold(title: str, body: str) -> str:
    return f'<details><summary>{escape(title)}</summary><div class="detail-body">{body}</div></details>'


def raw(value: Any) -> str:
    return f"<pre>{escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre>"


def behavior_heatmap(matrix: Sequence[dict[str, Any]], labels: Mapping[str, str]) -> str:
    """Row-normalized action counts; action equality is separate from behavior correctness."""
    counts: Counter[tuple[str, str]] = Counter()
    for row in matrix:
        counts[(str(row["expected"]), str(row["actual"]))] += row["count"]
    if not counts:
        return '<p class="empty">当前范围未记录回答行为。</p>'
    order = {
        key: i
        for i, key in enumerate(("answer", "clarify", "refuse", "answer_and_refuse", "none", "unknown"))
    }
    expected = sorted({a for a, _ in counts}, key=lambda value: (order.get(value, 99), value))
    actual = sorted({b for _, b in counts}, key=lambda value: (order.get(value, 99), value))
    body = '<div class="scroll"><table class="behavior-matrix" aria-label="预期与实际回答行为热力图">'
    body += "<caption>行是预期行为，列是实际行为；数字为轮数，小字为占本行比例。</caption>"
    body += '<thead><tr><th scope="col">预期 ↓ ／实际 →</th>'
    body += "".join(f'<th scope="col">{escape(labels.get(a, a))}</th>' for a in actual)
    body += "</tr></thead><tbody>"
    for wanted in expected:
        total = sum(counts[(wanted, a)] for a in actual)
        name = labels.get(wanted, wanted)
        body += f'<tr><th scope="row">{escape(name)}<small>共 {total} 轮</small></th>'
        for observed in actual:
            count = counts[(wanted, observed)]
            ratio = count / total if total else 0
            unknown = "unknown" in {wanted, observed}
            base = (112, 128, 149) if unknown else (36, 130, 120) if wanted == observed else (199, 116, 55)
            intensity = 0.12 + ratio * 0.55 if count else 0
            color = ",".join(str(round(255 + (value - 255) * intensity)) for value in base)
            actual_name = labels.get(observed, observed)
            description = f"预期{name}，实际{actual_name}：{count}轮，占本行{ratio:.1%}"
            body += (
                f'<td><button class="heat-cell" style="--heat:rgb({color})" '
                f'data-expected="{escape(wanted)}" data-actual="{escape(observed)}" '
                f'data-count="{count}" aria-label="{escape(description)}" '
                f'title="{escape(description)}" aria-pressed="false"'
                + (" disabled" if not count else "")
                + f"><strong>{count}</strong><span>{ratio:.1%}</span></button></td>"
            )
        body += "</tr>"
    return body + "</tbody></table></div>"


def render_report(
    report: dict[str, Any],
    cases: Sequence[dict[str, Any]],
    filters: Mapping[str, Any],
    labels: Mapping[str, str],
    scores: Mapping[str, str],
    dimensions: Sequence[str],
) -> str:
    counts, den = report["counts"], report["denominators"]
    source = report["source"]
    unit = "轮" if filters.get("content_unit") == "turn" else "题"
    prefix = "turn_score_" if unit == "轮" else "score_"
    valid = den["turn_quality_valid"] if unit == "轮" else den["quality_valid"]
    full, partial = counts.get(prefix + "2", 0), counts.get(prefix + "1", 0)
    missing = counts.get(prefix + "None", 0)
    active_filters = {k: v for k, v in filters.items() if v is not None and v != ""}
    generated = report.get("generated_at")
    generated_text = (
        datetime.fromtimestamp(generated, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        if isinstance(generated, (int, float))
        else generated
    )
    body = (
        '<header><p class="eyebrow">PHOTOBENCH · 离线分析报告</p><h1>任务表现与问题定位</h1>'
        f"<p>全部 {report.get('all_items', report['item_count'])} 题 · 本次导出 {len(cases)} 题"
        f" · 统计单位：{unit} · {'已筛选' if active_filters else '全部范围'}</p>"
        f'<div class="meta">Run：{escape(source.get("run_id"))}<br>'
        f"模型：{escape(source.get('model'))} · 规则版本：{escape(report['rule_version'])}"
        f" · 生成时间：{escape(generated_text)}</div></header>"
        '<nav aria-label="报告导航"><a href="#overview">总览</a><a href="#abilities">能力与题型</a>'
        '<a href="#behavior">回答行为</a><a href="#retrieval">检索与交付</a>'
        '<a href="#execution">运行异常</a>'
        '<a href="#cases">题目明细</a><a href="#provenance">来源与口径</a></nav>'
    )
    if report.get("stale"):
        body += '<p class="notice">来源已变化：本报告对应保存的分析快照，请结合底部来源指纹核对版本。</p>'
    if active_filters:
        readable = " · ".join(
            f"{labels.get(k, k)}：{labels.get(str(v), str(v))}" for k, v in active_filters.items()
        )
        body += fold("本次导出筛选条件", f'<p class="text">{escape(readable)}</p>')

    def kpi(title: str, value: str, note: str) -> str:
        return f'<div class="kpi">{title}<strong>{value}</strong><span>{note}</span></div>'

    body += '<section id="overview"><div class="kpis">'
    body += kpi(
        "任务满分率", f"{full / valid:.1%}" if valid else "未覆盖", f"{full} / {valid} 有效评分{unit}"
    )
    typed_summary = report.get("evaluation_summary") or {}
    records = [turn for case in cases for turn in selected_turns(case)] if unit == "轮" else list(cases)
    normalized: list[float] = []
    for record in records:
        result = record.get("evaluation_result") or {}
        value = (
            result.get("normalized_score")
            if result
            else (record["score"] / 2 if record.get("score") in (0, 1, 2) else None)
        )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            normalized.append(float(value))
    body += kpi(
        "平均答案正确性" if typed_summary.get("count") else "平均质量分",
        (f"{sum(normalized) / len(normalized):.1%}" if normalized else "未覆盖")
        if typed_summary.get("count")
        else (f"{(2 * full + partial) / valid:.3f} / 2" if valid else "未覆盖"),
        f"缺分 {missing}{unit}",
    )
    behavior_valid = den["behavior_valid"]
    correct = counts.get("behavior_correct", 0)
    body += kpi(
        "行为符合率",
        f"{correct / behavior_valid:.1%}" if behavior_valid else "未覆盖",
        f"{correct} / {behavior_valid} 有效判定轮",
    )
    body += kpi("本次导出题目", str(len(cases)), f"{den['turns']} 对话轮 · 列表按题去重")
    body += kpi(
        "存在运行异常", str(counts.get("affected_items", 0)), f"题 · {counts.get('events', 0)} 次异常事件"
    )
    body += kpi("最终执行失败", str(counts.get("execution_failed", 0)), "题 · 与任务质量分分别统计")
    overview_charts, dimension_charts, retrieval_charts, behavior_charts = render_charts(
        report, cases, filters, labels
    )
    body += '</div><div class="chart-grid">' + overview_charts + "</div>"
    body += evaluation_summary_html(typed_summary) + "</section>"
    body += '<section id="abilities"><div class="section-heading"><h2>能力与题型</h2>'
    body += '<p class="note">先看样本量，再比较满分率。得分类别按当前量表展示，连续分保留原值；'
    body += "多标签可重叠，不把它们绘成互斥饼图。</p></div>"
    body += (
        '<div class="report-legend">'
        + "".join(f'<span><i class="s{score}"></i>{escape(name)}</span>' for score, name in scores.items())
        + "</div>"
    )
    body += '<div class="chart-grid">' + dimension_charts + "</div>"
    body += "</section>" + score_composition(cases, filters, labels)
    body += '<section id="behavior" class="panel"><h2>回答行为：预期与实际</h2>'
    body += '<div class="chart-grid">' + behavior_charts + "</div>"
    body += behavior_heatmap(report["behavior_matrix"], labels)
    body += (
        '<div class="heat-legend"><span><i style="background:#248278"></i>同名行为</span>'
        '<span><i style="background:#c77437"></i>行为不一致</span>'
        '<span><i style="background:#708095"></i>行为未记录</span></div>'
        '<p class="note">颜色越深，占本行比例越高；点击有数据的格子查看对应题目。'
        "同名行为与行为Judge判定分开，符合率仍按有效判定轮数计算。</p>"
    )
    behavior_tables = ""
    for key, title in (("behavior_groups", "预期行为分布"), ("behavior_scenarios", "测试场景")):
        behavior_tables += f"<h3>{title}</h3>" + table(
            ["类别", "轮数", "占全部轮数", "符合", "不符合", "未判定", "符合率 / 有效判定"],
            [
                [
                    labels.get(r["value"], r["value"]),
                    r["total"],
                    f"{r['total'] / den['turns']:.1%}" if den["turns"] else "未覆盖",
                    r["correct"],
                    r["incorrect"],
                    r["missing"],
                    (f"{r['correct'] / r['valid']:.1%}" if r["valid"] else "未覆盖") + f" / {r['valid']}轮",
                ]
                for r in report.get(key, [])
            ],
        )
    behavior_tables += '<p class="note">同一轮可属于多个测试场景，场景占比可重叠。</p>'
    behavior_tables += table(
        ["预期", "实际", "轮数"],
        [
            [labels.get(r["expected"], r["expected"]), labels.get(r["actual"], r["actual"]), r["count"]]
            for r in report["behavior_matrix"]
        ],
    )
    body += fold("行为判定、测试场景与原始计数", behavior_tables) + "</section>"
    body += '<section id="retrieval"><div class="section-heading"><h2>检索与最终交付</h2>'
    body += '<p class="note">检索指标与原质量分分别统计。候选命中、最终交付和回答质量的关联'
    body += "仅作定位线索，不能直接证明失败原因。</p></div>"
    body += '<div class="chart-grid">' + retrieval_charts + "</div></section>"
    body += '<section id="execution" class="panel"><h2>运行异常分布</h2>'

    body += '<p class="note">异常类别可重叠；受影响题数、异常次数、最终失败及已恢复分别统计。</p>'
    body += (
        table(
            ["异常类别", "受影响题数", "异常次数", "最终失败题数", "已恢复题数"],
            [
                [r["label"], r["affected_items"], r["events"], r["final_failed_items"], r["recovered_items"]]
                for r in report["errors"]
            ],
        )
        if report["errors"]
        else '<p class="empty">当前范围未记录运行异常。</p>'
    )
    for key, dim, title in (
        ("ability_heatmap", "ability_family", "一级能力 × 运行异常（按题）"),
        ("heatmap", "content_type", "原内容题型 × 运行异常"),
    ):
        body += fold(
            title,
            table(
                ["类别", "异常代码", "受影响题数"],
                [
                    [
                        labels.get(r.get(dim, r.get("question_type", "unknown")), r.get(dim, "unknown")),
                        r["code"],
                        r["count"],
                    ]
                    for r in report.get(key, [])
                ],
            ),
        )
    body += "</section>"
    body += '<section id="cases" class="panel"><details id="case-reader" class="reader-shell">'
    body += f"<summary><div><h2>全部样例 · {len(cases):,}题</h2>"
    body += (
        f'<span class="note">完整嵌入本文件 · {den["turns"]:,}轮 · 展开后筛选与分页</span></div></summary>'
    )

    body += '<p class="note">展开查看逐轮问题、模型回答与本轮参考答案。配置和原始记录默认收起。'
    body += "本文件包含分析记录；完整模型与工具调用轨迹可在运行页另行导出。</p>"
    body += '<div id="reader-tools" hidden><div class="controls">'
    body += (
        '<label class="search">搜索全部轮次<input id="reader-search" type="search" '
        'placeholder="题号、问题、回答或参考答案"></label>'
    )
    body += '<label>题级质量分<select id="reader-score"><option value="">全部得分</option>'
    body += (
        "".join(f'<option value="{s}">{escape(n)}</option>' for s, n in scores.items()) + "</select></label>"
    )
    for field, title in (("ability", "一级能力"), ("execution", "执行结果")):
        body += f'<label>{title}<select id="reader-{field}"><option value="">全部</option></select></label>'
    for field, title in (("expected", "预期行为"), ("actual", "实际行为")):
        body += f'<label>{title}<select id="reader-{field}"><option value="">全部</option></select></label>'
    body += '<label>分析维度<select id="reader-dimension"><option value="">全部维度</option>'
    body += (
        "".join(
            f'<option value="{escape(dim)}">{escape(labels.get(dim, dim))}</option>'
            for dim in dimensions
            if dim != "ability_taxonomy_version"
        )
        + "</select></label>"
    )
    body += '<label>类别<select id="reader-category"><option value="">全部类别</option></select></label>'
    body += '<label>起始序号<input class="range-control" id="reader-from" type="number" min="1" '
    body += f'max="{max(1, len(cases))}" placeholder="1"></label>'
    body += '<label>结束序号<input class="range-control" id="reader-to" type="number" min="1" '
    body += f'max="{max(1, len(cases))}" placeholder="{len(cases)}"></label>'
    body += '<label>快捷筛选<select id="reader-behavior"><option value="">全部行为</option>'
    body += (
        '<option value="direct">预期澄清却直接回答</option></select></label>'
        '<button id="reader-reset">重置明细筛选</button></div>'
    )
    body += '<p class="note">序号按本次导出顺序从1开始。搜索和筛选只改变下方明细；'
    body += '上方统计始终对应本次导出范围。</p><p id="reader-scope" class="scope-note" hidden></p>'

    body += (
        '<div class="controls"><button id="reader-open">展开本页</button>'
        '<button id="reader-close">收起全部</button>'
    )
    body += '<span id="reader-count" role="status" aria-live="polite"></span></div></div>'
    body += (
        '<noscript><p class="notice">请启用浏览器 JavaScript 查看已嵌入的逐轮问答和筛选明细；'
        "本页无需联网。</p></noscript>"
    )
    for i, case in enumerate(cases):
        case_labels = {**labels, **case.get("ability_labels", {})}
        ability = " / ".join(
            case_labels.get(v, v) for v in case["dimensions"].get("ability_type", "unknown").split(" / ")
        )
        score_key = str(case["score"])
        body += (
            f'<details class="case-card" id="case-{i}"><summary><span class="case-identity">'
            f'<span class="badge s{score_key}">{escape(scores[score_key])}</span>'
            f'<span class="case-id">{escape(case["qa_id"])}</span></span>'
            f'<span class="case-question">{escape(case["question"])}<span class="case-meta">'
            f"{len(case['turns'])}轮对话 · {escape(ability)} · "
            f"{escape(labels.get(case['execution'], case['execution']))} · {len(case['events'])}次异常"
            "</span></span></summary></details>"
        )
    body += '<p class="empty" id="reader-empty" hidden>没有符合条件的题目，请调整筛选或搜索词。</p>'
    body += '<div class="pagination"><button id="reader-prev">上一页</button><span id="reader-page"></span>'
    body += '<button id="reader-next">下一页</button></div></details></section>'
    body += '<section id="provenance" class="panel"><h2>来源与统计口径</h2>'
    body += fold(
        "来源、运行配置与导出筛选",
        raw(
            {
                "source": source,
                "ability_taxonomy_versions": report.get("ability_taxonomy_versions"),
                "ability_display_projection": report.get("ability_display_projection"),
                "saved_rule_version": report.get("saved_rule_version"),
                "rule_version": report["rule_version"],
                "generated_at": report.get("generated_at"),
                "gt_fingerprint": report["gt_fingerprint"],
                "annotation_fingerprint": report.get("annotation_fingerprint"),
                "result_fingerprint": report.get("result_fingerprint"),
                "stale": report.get("stale", False),
                "filters": dict(filters),
            }
        ),
    )
    body += fold(
        "统计口径", "<ul>" + "".join(f"<li>{escape(s)}</li>" for s in report["definitions"]) + "</ul>"
    )
    body += fold(
        "全部计数与有效分母",
        table(["指标", "数量"], list(counts.items())) + table(["分母", "数量"], list(den.items())),
    )
    body += (
        '</section><p class="footer">PhotoBench · 本文件无需联网即可阅读、搜索和展开。'
        "分数与行为判定均来自保存记录。</p>"
    )
    display_keys = {str(c["execution"]) for c in cases}
    for c in cases:
        display_keys.update(c.get("dimension_labels", {}).get("ability_family", []))
        display_keys.update(str(t.get(k) or "unknown") for t in c["turns"] for k in ("expected", "actual"))
        display_keys.update(str(e.get("recovery")) for e in c["events"])
    display_keys.update(dimensions)
    for c in cases:
        for dim in dimensions:
            display_keys.update(c.get("dimension_labels", {}).get(dim, []))
    reader_labels = {k: v for k, v in labels.items() if k in display_keys}
    payload = json.dumps(
        {
            "cases": cases,
            "labels": reader_labels,
            "scores": scores,
            "content_unit": filters.get("content_unit", "qa"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # A question can contain HTML or a closing script tag; keep it inert in the data block.
    for char, escaped in (
        ("&", "\\u0026"),
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        payload = payload.replace(char, escaped)
    body += f'<script type="application/json" id="report-data">{payload}</script>'
    return document(body, controller=True)
