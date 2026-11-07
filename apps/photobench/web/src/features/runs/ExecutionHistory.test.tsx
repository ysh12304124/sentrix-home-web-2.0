import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../api/client.ts';
import type { Run } from '../../api/types.ts';
import { ExecutionHistory } from './ExecutionHistory.tsx';
vi.mock('../../api/client.ts', () => ({api:vi.fn()}));
afterEach(() => {cleanup();vi.clearAllMocks();});

const run:Run = {run_id:'new',status:'completed_with_errors',qa_count:1171,item_count:1171,fatal_error:'旧轮错误',
  rejudge:{rejudge_id:'score',status:'completed',total:85,completed:85,failed:0,qa_count:53,turn_count:61,results:[]},
  execution_versions:[
    {execution_id:'old',run_id:'old',kind:'answers',status:'failed',answer_count:882},
    {execution_id:'new',run_id:'new',kind:'answers',source_run_id:'old',status:'completed_with_errors',answer_count:1171,inherited_count:1088},
    {execution_id:'score',run_id:'new',kind:'scoring',status:'completed',total:85,completed:85,failed:0,qa_count:53,turn_count:61},
  ]};
it('defaults to latest success with scoring range and cumulative unique QA, hides old errors', () => {
  render(<ExecutionHistory run={run}/>);
  expect((screen.getByRole('combobox',{name:'执行版本'}) as HTMLSelectElement).value).toBe('score');
  expect(screen.getByText(/53 题 \/ 61 轮/)).toBeTruthy();
  expect(screen.getByText(/当前累计保存 1171\/1171/)).toBeTruthy();
  expect(screen.queryByText('旧轮错误')).toBeNull();
  expect(vi.mocked(api)).not.toHaveBeenCalled();
});
it('selects one historical log while cumulative results remain the current answer sheet', async () => {
  vi.mocked(api).mockResolvedValue({run_id:'old',status:'failed',fatal_error:'写盘失败'});
  render(<ExecutionHistory run={run}/>);
  fireEvent.change(screen.getByRole('combobox',{name:'执行版本'}),{target:{value:'old'}});
  await waitFor(() => expect(api).toHaveBeenCalledWith('/runs/old?include_history=false',expect.anything()));
  await act(async () => {});
  expect(screen.getByText('写盘失败')).toBeTruthy();
  expect(screen.getByText(/当前累计保存 1171\/1171/)).toBeTruthy();
  expect(screen.queryByText(/53 题 \/ 61 轮/)).toBeNull();
  fireEvent.change(screen.getByRole('combobox',{name:'执行版本'}),{target:{value:'new'}});
  expect(screen.getByText('83')).toBeTruthy();
  expect(screen.queryByText('写盘失败')).toBeNull();
});
