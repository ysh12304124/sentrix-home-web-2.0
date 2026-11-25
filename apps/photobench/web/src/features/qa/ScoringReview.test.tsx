import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest';
import { ScoringReview } from './ScoringReview.tsx';
vi.mock('../../api/client.ts',()=>({post:vi.fn()}));
import { post } from '../../api/client.ts';

describe('scoring audits',()=>{
  beforeEach(()=>vi.clearAllMocks());
  afterEach(cleanup);
  it('pins the chosen zero-score batch and never submits a replacement score',async()=>{
    vi.mocked(post).mockResolvedValue({scoring_reviews:[{qa_id:'q',review_id:'a',verdict:'reason_error_score_unchanged',reviewer:'陈',note:'日期正确但遗漏地点'}]});
    render(<ScoringReview runId="r" results={[{key:'b:q:0',qa_id:'q',batch_id:'b',profile_id:'atm_qs_v1',turn_index:0,answer_fingerprint:'sha',score:0}]} reviews={[]}/>);
    expect(post).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('复核结论'),{target:{value:'reason_error_score_unchanged'}});
    fireEvent.change(screen.getByLabelText('审核人'),{target:{value:'陈'}});
    fireEvent.change(screen.getByLabelText('复核依据'),{target:{value:'日期正确但遗漏地点'}});
    fireEvent.click(screen.getByRole('button',{name:'保存复核记录'}));
    expect(await screen.findByText('日期正确但遗漏地点')).toBeTruthy();
    expect(post).toHaveBeenCalledWith('/runs/r/reviews',{scoring_reviews:[{qa_id:'q',batch_id:'b',profile_id:'atm_qs_v1',turn_index:0,answer_fingerprint:'sha',verdict:'reason_error_score_unchanged',reviewer:'陈',note:'日期正确但遗漏地点'}]});
  });
  it('compares historical batches with frozen configuration and structured reasons',()=>{
    const base = {qa_id:'q',profile_id:'atm_qs_v1',turn_index:0,answer_fingerprint:'sha',max_score:1,status:'completed'};
    render(<ScoringReview runId="r" results={[
      {...base,key:'old:q:0',batch_id:'old',score:0,reason:'旧理由',batch_definition:{rule_version:'v4',judge:{model:'mix'},prompts_sha256:'prompt-old'}},
      {...base,key:'new:q:0',batch_id:'new',score:1,reason:'核心匹配',checks:{core_match:true,missing:[],contradictions:[],unverified_additions:['副标题']},batch_definition:{rule_version:'v5',judge:{model:'fixed'},prompts_sha256:'prompt-new'}}
    ]} reviews={[]}/>);
    expect(screen.getByText('新旧批次对照')).toBeTruthy();
    expect(screen.getByText('旧理由')).toBeTruthy();
    expect(screen.getByText('副标题')).toBeTruthy();
    expect(screen.getByText('prompt-new')).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
  });

});
