import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { InteractiveModel } from '../../api/types.ts';
import { ModelSelect } from './ModelSelect.tsx';
import { RetestDialog } from './RetestDialog.tsx';

const { api, post } = vi.hoisted(() => ({ api: vi.fn(), post: vi.fn() }));
vi.mock('../../api/client.ts', () => ({ api, post, BASE: '/api/v1' }));
const options = { profiles: [], targets: {}, managerUrl: '', modelEndpoint: 'http://one/v1' };

function Harness() {
  const [value, setValue] = useState<InteractiveModel>({ base_url: 'http://one/v1', endpoint_model: '' });
  const [ready, setReady] = useState(false);
  return <><ModelSelect options={options} value={value} onChange={setValue} onReadyChange={setReady}/><button disabled={!ready}>提交</button></>;
}
beforeEach(() => { post.mockReset(); api.mockReset(); api.mockResolvedValue({ items: [] }); });
afterEach(cleanup);

it('shows the actual deployed model without changing the current selection', async () => {
  api.mockResolvedValue({ served_model_name: 'actual-model', served_models: ['actual-model'] });
  const onChange = vi.fn();
  render(<ModelSelect options={options} value={{}} onChange={onChange}/>);
  await screen.findByRole('option', { name: '当前部署模型 · actual-model' });
  expect(api).toHaveBeenCalledWith('/current-deployed-model');
  expect(onChange).not.toHaveBeenCalled();
});

it('shows a read error instead of inventing a deployed model name', async () => {
  api.mockRejectedValue(new Error('连接失败'));
  render(<ModelSelect options={options} value={{}} onChange={vi.fn()}/>);
  await screen.findByRole('option', { name: '当前部署模型 · 暂未获取型号' });
  expect(screen.getByRole('alert').textContent).toContain('连接失败');
});

it('only offers models returned by the endpoint and requires a choice when several are available', async () => {
  post.mockResolvedValue({ served_models: ['first', 'second'], selection_required: true });
  render(<Harness/>);
  await screen.findByRole('option', { name: 'second' });
  expect((screen.getByText('提交') as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('问答端点模型'), { target: { value: 'second' } });
  await waitFor(() => expect((screen.getByText('提交') as HTMLButtonElement).disabled).toBe(false));
  expect(screen.queryByRole('textbox', { name: /模型名/ })).toBeNull();
});

it('ignores an old endpoint response after the address changes', async () => {
  let oldResponse!: (value: { served_models: string[] }) => void;
  post.mockImplementation((_path: string, body: { model_base_url: string }) => body.model_base_url === 'http://one/v1'
    ? new Promise(resolve => { oldResponse = resolve; }) : Promise.resolve({ served_models: ['new-model'] }));
  render(<Harness/>);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByLabelText('问答模型地址'), { target: { value: 'http://two/v1' } });
  await screen.findByRole('option', { name: 'new-model' });
  oldResponse({ served_models: ['old-model'] });
  await waitFor(() => expect((screen.getByLabelText('问答端点模型') as HTMLSelectElement).value).toBe('new-model'));
  expect(screen.queryByRole('option', { name: 'old-model' })).toBeNull();
});

it('shows discovery errors and allows an explicit refresh', async () => {
  post.mockRejectedValueOnce(new Error('模型接口不可用')).mockResolvedValueOnce({ served_models: ['recovered'] });
  render(<Harness/>);
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '模型接口不可用');
  expect((screen.getByText('提交') as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '↗ 获取可用模型' }));
  await screen.findByRole('option', { name: 'recovered' });
  await waitFor(() => expect((screen.getByText('提交') as HTMLButtonElement).disabled).toBe(false));
});

it('waits for discovery before allowing a retest and submits the returned model ID', async () => {
  post.mockImplementation((path: string) => Promise.resolve(path === '/current-model'
    ? { served_models: ['actual-served-id'] } : { run_id: 'new' }));
  render(<RetestDialog run={{ run_id: 'source' }} question={{ index: 0, qa_id: 'q1' }} models={options} busy={false} onClose={vi.fn()}/>);
  fireEvent.change(screen.getByLabelText('问答模型'), { target: { value: 'custom' } });
  expect((screen.getByText('开始重测') as HTMLButtonElement).disabled).toBe(true);
  await waitFor(() => expect((screen.getByText('开始重测') as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByText('开始重测'));
  await waitFor(() => expect(post).toHaveBeenCalledWith('/runs/source/retest', expect.objectContaining({
    model: { base_url: 'http://one/v1', endpoint_model: 'actual-served-id' },
  })));
});
