import { cleanup,render,screen,fireEvent } from '@testing-library/react';
import { afterEach,beforeEach,describe,it,expect,vi } from 'vitest';
import { ProcessDiagnostics, ProcessDiagnosticsOverview, type ProcessDiagnostic } from './ProcessDiagnostics.tsx';
vi.mock('../../api/client.ts',()=>({api:vi.fn()}));
import { api } from '../../api/client.ts';
describe('saved process observations',()=>{
 afterEach(cleanup);
 beforeEach(()=>vi.clearAllMocks());
 it('passes scoring context and resets the report when the batch changes',async()=>{
  vi.mocked(api).mockResolvedValue({scoring_context:{batches:[{profile_id:'atm_qs_v1',batch_id:'new'}]},summary:{analyzed_qa:1,total_qa:1,original_answered_with_gap:0,categories:{},labels:{},statuses:{}}});
  const {rerender}=render(<ProcessDiagnosticsOverview runId="r" scoringProfile="atm_qs_v1" scoringBatch="old"/>);
  const details=screen.getByText('答题过程诊断汇总').closest('details')!;details.open=true;fireEvent(details,new Event('toggle'));
  await screen.findByText(/ATM-Bench式QS · new/);
  expect(api).toHaveBeenCalledWith('/runs/r/process-diagnostics?scoring_profile=atm_qs_v1&scoring_batch=old');
  rerender(<ProcessDiagnosticsOverview runId="r" scoringProfile="atm_qs_v1" scoringBatch="new"/>);
  fireEvent(details,new Event('toggle'));
  await screen.findByText(/ATM-Bench式QS · new/);
  expect(api).toHaveBeenCalledWith('/runs/r/process-diagnostics?scoring_profile=atm_qs_v1&scoring_batch=new');
 });
 it('keeps observations collapsed and does not turn no_text into scoring failure',()=>{
  const value:ProcessDiagnostic={rule_version:'v1',qa_id:'q',answer_fingerprint:'sha',original_answered_with_gap:true,chain:[],findings:[{category:'no_text',label:'工具未读出文字',status:'confirmed',message:'是否影响回答需复核',sources:['tool_trace[0]']}]};
  render(<ProcessDiagnostics value={value}/>);
  expect(screen.getByText('答题过程诊断').closest('details')?.open).toBe(false);
  expect(screen.getByText('跳到本题执行日志').getAttribute('href')).toBe('#process-log-q');
  expect(screen.getByText('是否影响回答需复核')).toBeTruthy();
 });
 it('reads diagnostics only when opened and reports unique overlapping QA counts',async()=>{
  vi.mocked(api).mockResolvedValue({summary:{analyzed_qa:631,total_qa:631,original_answered_with_gap:610,categories:{no_text:58},labels:{no_text:'工具未读出文字'},statuses:{record_missing:2}}});
  render(<ProcessDiagnosticsOverview runId="r"/>);
  expect(api).not.toHaveBeenCalled();
  const details=screen.getByText('答题过程诊断汇总').closest('details')!;details.open=true;fireEvent(details,new Event('toggle'));
  expect(await screen.findByText('工具未读出文字')).toBeTruthy();
  expect(api).toHaveBeenCalledWith('/runs/r/process-diagnostics');
  expect(screen.getByText(/各类不能相加/)).toBeTruthy();
 });
});
