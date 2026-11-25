import { useEffect, useRef, useState } from 'react';
import { api, post } from '../../api/client.ts';
import type { CurrentModelInfo, InteractiveModel } from '../../api/types.ts';

export interface ModelOptions {
  profiles: Array<{ id: string; available?: boolean; label?: string }>;
  targets: Record<string, { label?: unknown; model_base_url?: unknown; manager_url?: unknown }>;
  managerUrl: string;
  modelEndpoint: string;
}

export function ModelSelect({ options, value, onChange, onReadyChange, disabled = false }: {
  options: ModelOptions; value: InteractiveModel; onChange: (value: InteractiveModel) => void;
  onReadyChange?: (ready: boolean) => void; disabled?: boolean;
}) {
  const choice = value.profile && value.profile !== '__current__' ? 'profile:' + value.profile : value.target_id ? 'target:' + value.target_id : value.base_url !== undefined ? 'custom' : 'current';
  const endpoint = (value.base_url || '').trim();
  const [deployed, setDeployed] = useState<CurrentModelInfo | null>(null);
  const [deployedError, setDeployedError] = useState('');
  useEffect(() => {
    if (choice !== 'current') return;
    let disposed = false;
    void api<CurrentModelInfo>('/current-deployed-model').then(data => {
      if (!disposed) { setDeployed(data); setDeployedError(''); }
    }).catch((error: Error) => {
      if (!disposed) { setDeployed(null); setDeployedError(error.message); }
    });
    return () => { disposed = true; };
  }, [choice]);
  const deployedName = deployed?.served_model_name || deployed?.served_models?.join(' / ');
  const [discovery, setDiscovery] = useState<{ endpoint: string; models: string[]; error: string; revision: number } | null>(null);
  const [revision, setRevision] = useState(0);
  const latest = useRef({ value, onChange });
  useEffect(() => { latest.current = { value, onChange }; }, [value, onChange]);
  useEffect(() => {
    if (choice !== 'custom' || !endpoint) return;
    let disposed = false;
    const timer = window.setTimeout(() => {
      void post<CurrentModelInfo>('/current-model', { model_base_url: endpoint, vllm_target_id: '' }).then(data => {
        if (disposed) return;
        const models = data.served_models || [];
        setDiscovery({ endpoint, models, error: models.length ? '' : '该接口未返回可用模型', revision });
        const current = latest.current;
        if (models.length === 1 && !current.value.endpoint_model) {
          current.onChange({ ...current.value, endpoint_model: models[0] });
        }
      }).catch((error: Error) => {
        if (!disposed) setDiscovery({ endpoint, models: [], error: error.message, revision });
      });
    }, 350);
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [choice, endpoint, revision]);
  const resolved = discovery?.endpoint === endpoint && discovery.revision === revision;
  const servedModels = resolved ? discovery.models : [];
  const ready = choice !== 'custom' || Boolean(endpoint && servedModels.includes(value.endpoint_model || ''));
  useEffect(() => { onReadyChange?.(ready); }, [ready, onReadyChange]);
  return <div className="interactive-model">
    <label>模型<select aria-label="问答模型" value={choice} disabled={disabled} onChange={e => {
      const next = e.target.value;
      if (next.startsWith('profile:')) onChange({ profile: next.slice(8), base_url: options.modelEndpoint, manager_url: options.managerUrl });
      else if (next.startsWith('target:')) onChange({ target_id: next.slice(7) });
      else if (next === 'custom') onChange({ base_url: options.modelEndpoint, endpoint_model: '' });
      else onChange({});
    }}>
      <option value="current">当前部署模型 · {deployedName || (deployedError ? '暂未获取型号' : '正在读取…')}</option>
      {options.profiles.map(p => <option key={p.id} value={'profile:' + p.id} disabled={p.available === false}>{p.label || p.id}</option>)}
      {Object.entries(options.targets).map(([id, target]) => <option key={id} value={'target:' + id}>{String(target.label || id)}</option>)}
      <option value="custom">其他模型端点</option>
    </select></label>
    {choice === 'current' && deployedError && <p role="alert" className="error interactive-model-message">读取当前模型失败：{deployedError}</p>}
    {choice === 'custom' && <>
      <label>模型服务地址<input aria-label="问答模型地址" value={value.base_url || ''} placeholder="http://host:port/v1" disabled={disabled} onChange={e => onChange({ ...value, base_url: e.target.value, endpoint_model: '' })}/></label>
      <button className="btn ghost compact" type="button" disabled={disabled || !endpoint || !resolved} onClick={() => setRevision(n => n + 1)}>{endpoint && !resolved ? '正在获取模型…' : '↗ 获取可用模型'}</button>
      <label>可用模型<select aria-label="问答端点模型" value={servedModels.includes(value.endpoint_model || '') ? value.endpoint_model : ''} disabled={disabled || !servedModels.length} onChange={e => onChange({ ...value, endpoint_model: e.target.value })}>
        <option value="">{endpoint && !resolved ? '正在获取…' : servedModels.length ? '请选择模型' : '暂无可用模型'}</option>
        {servedModels.map(model => <option key={model} value={model}>{model}</option>)}
      </select></label>
      {resolved && discovery.error && <p role="alert" className="error interactive-model-message">{discovery.error}</p>}
      {resolved && servedModels.length > 1 && !ready && <p className="muted small interactive-model-message">该接口提供多个模型，请选择本次使用的模型。</p>}
    </>}
  </div>;
}
