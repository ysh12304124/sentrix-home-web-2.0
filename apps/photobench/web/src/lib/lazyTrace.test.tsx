import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, renderHook, waitFor } from '@testing-library/react';
import * as client from '../api/client.ts';
import { readTraceOverview, useTraceOverview } from './lazyTrace.ts';

afterEach(()=>{cleanup();vi.restoreAllMocks()});
it('旧服务仅读分页摘要求统计，不下载正文，工具内部模型不重复计时',async()=>{
  const request=vi.spyOn(client,'api').mockImplementation(async path=>{
    const url=String(path);
    if(url.includes('kind=sources')) return {nodes:[
      {kind:'metrics',metadata:{duration_ms:100,call_type:'agent'}},
      {kind:'metrics',metadata:{duration_ms:500,call_type:'tool_internal'}}],total:2};
    if(url.includes('offset=50')) return {nodes:[{kind:'tool',metadata:{duration_ms:200}}],total:51};
    return {nodes:[{kind:'model',metadata:{duration_ms:100}}],total:51,next_offset:50};
  });
  expect(await readTraceOverview({index:0,run_id:'r',trace_storage:{version:3,revision:'pinned'}},new AbortController().signal)).toEqual({model_call_count:2,tool_call_count:1,model_ms:100,tool_ms:200,timing_complete:true});
  expect(request).toHaveBeenCalledTimes(3);
  for(const [url] of request.mock.calls){expect(String(url)).toContain('revision=pinned');expect(String(url)).not.toContain('/trace/t')}
});
it('新服务轻量统计直接使用，不再为统计请求节点或诊断',async()=>{
  const request=vi.spyOn(client,'api');
  const summary={model_call_count:25,tool_call_count:16,model_ms:1000,tool_ms:2000,timing_complete:true};
  const {result}=renderHook(()=>useTraceOverview({index:0,run_id:'r',trace_storage:{version:3},trace_read_summary:summary}));
  await waitFor(()=>expect(result.current.overview).toBe(summary));
  expect(request).not.toHaveBeenCalled();
});
