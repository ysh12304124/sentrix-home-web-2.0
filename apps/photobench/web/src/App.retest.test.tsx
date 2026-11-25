import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const { api } = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('./api/client.ts', () => ({ api, post: vi.fn(), BASE: '/api/v1' }));
vi.mock('./features/qa/QaResults.tsx', () => ({
  QaResults: ({ onRetest }: { onRetest: (question: { index: number; qa_id: string }) => void }) =>
    <button onClick={() => onRetest({ index: 0, qa_id: 'q1' })}>打开重测</button>,
}));
vi.mock('./features/interactive/RetestDialog.tsx', () => ({
  RetestDialog: ({ onClose }: { onClose: () => void }) => <button onClick={onClose}>关闭重测</button>,
}));

import { App } from './App.tsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('关闭重测后持续刷新取消状态，聊天发送按钮恢复可用', async () => {
  Element.prototype.scrollIntoView = vi.fn();
  localStorage.clear();
  let closing = false, closingReads = 0;
  const original = { run_id: 'source', status: 'completed', mode: 'reuse', model_name: 'original' };
  api.mockImplementation(async (path: string) => {
    if (path === '/config') return { runtime_config: {}, vllm_targets: {} };
    if (path === '/manifests') return { manifests: [] };
    if (path === '/active-work') return { work: [], cleanup_status: 'stopped' };
    if (path.startsWith('/runs?')) {
      const cancelled = closing && ++closingReads > 1;
      return { runs: closing ? [original, { run_id: 'retest', task_id: 'retest-task', status: cancelled ? 'cancelled' : 'cancelling' }] : [original],
        page: 1, pages: 1, total: closing ? 2 : 1, active_count: closing && !cancelled ? 1 : 0 };
    }
    if (path.startsWith('/runs/source?')) return original;
    if (path.startsWith('/runs/source/items')) return { items: [], page: 1, pages: 1, total: 0 };
    if (path.startsWith('/memory-spaces')) return { spaces: [{ id: 'scope', name: '相册' }], reuse_bases: [] };
    if (path === '/chat/sessions') return { items: [] };
    return {};
  });
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ kinds: [] }))));
  const { container } = render(<App />);
  await waitFor(() => expect(container.querySelector('.run-row')).not.toBeNull());
  fireEvent.click(container.querySelector('.run-row')!);
  fireEvent.click(await screen.findByRole('button', { name: '打开重测' }));
  closing = true;
  fireEvent.click(screen.getByRole('button', { name: '关闭重测' }));
  await waitFor(() => expect(closingReads).toBeGreaterThan(0));
  fireEvent.click(screen.getByRole('button', { name: /^聊天对话$/ }));
  fireEvent.change(await screen.findByRole('textbox', { name: '聊天问题' }), { target: { value: '查找照片' } });
  await waitFor(() => expect((screen.getByRole('button', { name: /^发送$/ }) as HTMLButtonElement).disabled).toBe(false), { timeout: 4000 });
});
