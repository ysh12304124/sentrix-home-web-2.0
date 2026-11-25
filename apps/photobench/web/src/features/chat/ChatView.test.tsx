import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ManifestSummary } from '../../api/types.ts';
import { ChatView } from './ChatView.tsx';

const { api, post } = vi.hoisted(() => ({ api: vi.fn(), post: vi.fn() }));
vi.mock('../../api/client.ts', () => ({ api, post, BASE: '/api/v1' }));
afterEach(cleanup);
beforeEach(() => { vi.resetAllMocks(); localStorage.clear(); });

const options = { profiles: [], targets: {}, managerUrl: '', modelEndpoint: '' };
function savedChats(status = 'ready') {
  const sessions = [
    { session_id: 'one', title: '第一段对话', status, scope_id: 'scope', album_id: 'album', turns: [], items: [] },
    { session_id: 'two', title: '第二段对话', status: 'cancelled', scope_id: 'scope', album_id: 'album', turns: [], items: [] },
  ];
  api.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path.startsWith('/memory-spaces')) return { spaces: [{ id: 'scope', name: '相册' }], reuse_bases: [] };
    if (path === '/chat/sessions') return { items: sessions };
    if (init?.method === 'DELETE') return {};
    return sessions.find(s => path.split('?')[0].endsWith('/' + s.session_id));
  });
  return render(<ChatView sentrixUrl="http://product" manifests={[]} models={options} busy={false} onOpenMedia={vi.fn()}/>);
}

it.each(['ready', 'running'])('restores a %s conversation without opening its trace on entering or returning to chat', async status => {
  localStorage.setItem('photobench-chat-session', 'one');
  const first = savedChats(status);
  await screen.findByRole('button', { name: '打开对话与轨迹' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(localStorage.getItem('photobench-chat-session')).toBe('one');
  first.unmount();
  savedChats(status);
  const open = await screen.findByRole('button', { name: '打开对话与轨迹' });
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(open);
  expect(screen.getByRole('dialog', { name: '聊天对话与执行轨迹' })).toBeTruthy();
});

it('opens the conversation when a history entry is explicitly selected', async () => {
  savedChats();
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(await screen.findByRole('button', { name: /^第二段对话/ }));
  expect(await screen.findByRole('dialog', { name: '聊天对话与执行轨迹' })).toBeTruthy();
});

it('shows saved messages without loading traces or rediscovering the historical model and continues the same session', async () => {
  const session = { session_id: 'old', title: '历史聊天', status: 'ready', scope_id: 'scope', album_id: 'album', model_profile: '__current__', model_name: 'old-model', runtime_snapshot: { model_base_url: 'http://old/v1' }, turns: [{ index: 0, turn_id: 't1', message: '过去的问题', answer: '过去的回答', status: 'complete' }] };
  api.mockImplementation(async (path: string) => {
    if (path.startsWith('/memory-spaces')) return { spaces: [{ id: 'scope' }], reuse_bases: [] };
    if (path === '/chat/sessions') return { items: [session] };
    if (path === '/current-deployed-model') return { served_models: ['new-model'] };
    if (path.includes('/turns/0')) return { index: 0, final_answer: '过去的回答', runtime_turns: [] };
    if (path.includes('/chat/sessions/old')) return session;
    throw new Error(path);
  });
  post.mockResolvedValue({ ...session, status: 'preparing' });
  render(<ChatView sentrixUrl="http://product" manifests={[]} models={options} busy={false} onOpenMedia={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button', { name: /^历史聊天/ }));
  expect(await screen.findByText('过去的回答')).toBeTruthy();
  expect(screen.getByText('过去的问题')).toBeTruthy();
  expect(api.mock.calls.some(([path]) => path.includes('/turns/'))).toBe(false);
  expect(post).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('聊天问题'), { target: { value: '继续问' } });
  fireEvent.click(screen.getByRole('button', { name: '发送' }));
  await waitFor(() => expect(post).toHaveBeenCalledWith('/chat/sessions/old/turns', { message: '继续问', reasoning: false }));
});

it('opens history immediately, isolates a slow turn read and allows a failed turn to retry', async () => {
  let rejectTurn: (error: Error) => void = () => {};
  const session = { session_id: 'old', title: '慢详情', status: 'ready', scope_id: 'scope', album_id: 'album', turns: [{ index: 0, turn_id: 't1', message: '已存问题', status: 'cancelled' }] };
  api.mockImplementation(async (path: string) => {
    if (path.startsWith('/memory-spaces')) return { spaces: [{ id: 'scope' }], reuse_bases: [] };
    if (path === '/chat/sessions') return { items: [session] };
    if (path.includes('/turns/0')) return new Promise((_, reject) => { rejectTurn = reject; });
    if (path.includes('/chat/sessions/old')) return session;
    return {};
  });
  render(<ChatView sentrixUrl="http://product" manifests={[]} models={options} busy={false} onOpenMedia={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button', { name: /^慢详情/ }));
  expect(await screen.findByText('已存问题')).toBeTruthy();
  expect(screen.getByRole('dialog')).toBeTruthy();
  expect((screen.getByLabelText('聊天问题') as HTMLTextAreaElement).disabled).toBe(false);
  await waitFor(() => expect(api.mock.calls.some(([path]) => path.includes('/turns/0'))).toBe(true));
  rejectTurn(new Error('详情超时'));
  expect(await screen.findByText(/此回合读取失败：详情超时/)).toBeTruthy();
  api.mockResolvedValueOnce({ index: 0, final_answer: '找回的回答' });
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('找回的回答')).toBeTruthy();
});

it('ignores late errors after switching history and keeps viewing available during evaluations', async () => {
  let rejectFirst: (error: Error) => void = () => {};
  const sessions = ['one', 'two'].map(id => ({ session_id: id, title: id, status: 'ready', scope_id: 'scope', turns: [] }));
  api.mockImplementation(async (path: string) => {
    if (path.startsWith('/memory-spaces')) return { spaces: [], reuse_bases: [] };
    if (path === '/chat/sessions') return { items: sessions };
    if (path.includes('/sessions/one')) return new Promise((_, reject) => { rejectFirst = reject; });
    if (path.includes('/sessions/two')) return sessions[1];
    return {};
  });
  render(<ChatView sentrixUrl="http://product" manifests={[]} models={options} busy onOpenMedia={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button', { name: /^one/ }));
  expect(screen.getByText('正在读取对话消息…')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /^two/ }));
  await screen.findByText('会话模型：已保存的运行配置');
  rejectFirst(new Error('旧请求超时'));
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  expect(screen.getByText(/测评任务正在使用服务/)).toBeTruthy();
});

it('confirms deletion, clears the selected conversation and its saved selection', async () => {
  localStorage.setItem('photobench-chat-session', 'one');
  savedChats();
  await screen.findByRole('button', { name: '打开对话与轨迹' });
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(await screen.findByLabelText('删除对话：第一段对话'));
  expect(api.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(screen.queryByRole('button', { name: '确认删除' })).toBeNull();
  fireEvent.click(screen.getByLabelText('删除对话：第一段对话'));
  fireEvent.click(screen.getByRole('button', { name: '确认删除' }));
  await waitFor(() => expect(screen.queryByText('第一段对话')).toBeNull());
  expect(api).toHaveBeenCalledWith('/chat/sessions/one', { method: 'DELETE' });
  expect(localStorage.getItem('photobench-chat-session')).toBeNull();
  expect(screen.getByText('第二段对话')).toBeTruthy();
});

it('keeps the conversation and confirmation on delete failure', async () => {
  savedChats();
  fireEvent.click(await screen.findByLabelText('删除对话：第二段对话'));
  api.mockRejectedValueOnce(new Error('产品服务暂不可用'));
  fireEvent.click(screen.getByRole('button', { name: '确认删除' }));
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.getByText('第二段对话')).toBeTruthy();
  expect(screen.getByRole('button', { name: '确认删除' })).toBeTruthy();
});

it('requires active conversations to stop before deleting them', async () => {
  savedChats('running');
  const button = await screen.findByLabelText('删除对话：第一段对话') as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  expect((screen.getByLabelText('删除对话：第二段对话') as HTMLButtonElement).disabled).toBe(false);
});

it('opens the scope dataset with its valid question set before fetching and searches its rows', async () => {
  localStorage.clear();
  const manifests = [
    { album_id: 'album3', qa_sets: ['old-set'] },
    { album_id: 'mixed', qa_sets: ['mixed-set'] },
  ] as ManifestSummary[];
  api.mockImplementation(async (path: string) => {
    if (path === '/current-deployed-model') return { served_models: ['test-model'] };
    if (path.startsWith('/memory-spaces')) return { spaces: [{ id: 'scope', name: '相册' }], reuse_bases: [{ scope_id: 'scope', album_id: 'mixed' }] };
    if (path === '/chat/sessions') return { items: [] };
    if (path.startsWith('/qa-dataset?album_id=mixed&qa_set=mixed-set&')) return { items: [{ qa_id: 'mixed1', question: '婚礼照片', answer: '舞台' }] };
    throw new Error('invalid album/question-set pair');
  });
  render(<ChatView sentrixUrl="http://product" manifests={manifests} models={{ profiles: [], targets: {}, managerUrl: '', modelEndpoint: '' }} busy={false} onOpenMedia={vi.fn()}/>);
  await screen.findByRole('option', { name: '相册' });
  fireEvent.click(screen.getByText('从题集选题'));
  await screen.findByRole('button', { name: 'mixed1 婚礼照片' });
  await waitFor(() => expect(api.mock.calls.filter(([path]) => path.startsWith('/qa-dataset'))).toHaveLength(1));
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.change(screen.getByLabelText('搜索聊天题目'), { target: { value: '没有的题' } });
  expect(screen.queryByRole('button', { name: 'mixed1 婚礼照片' })).toBeNull();
  fireEvent.change(screen.getByLabelText('搜索聊天题目'), { target: { value: 'mixed1' } });
  fireEvent.click(screen.getByRole('button', { name: 'mixed1 婚礼照片' }));
  expect(screen.getByText('标准答案：舞台')).toBeTruthy();
});
