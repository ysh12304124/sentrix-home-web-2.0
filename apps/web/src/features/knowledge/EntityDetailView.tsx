/**
 * 实体详情。形状对齐 1.0 `src/app.js:1947-1948` 的 `openEntity` + `:1016` 的实体弹层：
 * 当前属性 / 关联事件与关系 / 原始证据，三块都用 `GET /entities/{id}` 真的返回的数据。
 *
 * **地点属性编辑**只对 `entity_type === "place"` 出现，写的是 1.0 那两个用户维护键
 * （`alias` / `private_flag`）。走 `PUT /entities/{id}/properties/{key}`；后端会顶掉
 * 派生值，前端不自己算合并规则。
 */

import { useState, type FormEvent } from "react";

import {
  getEntity,
  updateEntityProperty,
  type AssetDetailResponse,
  type EntityDetailFullResponse,
  type EntityPropertyPayload,
  type ObservationPayload,
} from "../../shared/api/client";
import { AssetDetail } from "../library/AssetDetail";
import { assetFromObservation } from "./observationAsset";
import "./knowledge.css";
import { MemoryCorrection, CorrectionHistory } from '../../shared/components/MemoryCorrection';

function propertyValue(property: EntityPropertyPayload): string {
  if (typeof property.value === "boolean") return property.value ? "已开启" : "未开启";
  if (Array.isArray(property.value)) return property.value.map(String).join("、");
  return property.value === null || property.value === undefined
    ? "未设置"
    : String(property.value);
}

function findProperty(
  properties: readonly EntityPropertyPayload[],
  key: string,
): EntityPropertyPayload | undefined {
  return properties.find((property) => property.property_key === key);
}

/**
 * 地点属性编辑器。1.0 的表单有「自定义别名 / 私密地点 / 支撑证据」三项
 * （`src/app.js:1039`），这里保持同一组字段。
 */
function PlacePropertyEditor({
  detail,
  onSaved,
}: {
  detail: EntityDetailFullResponse;
  onSaved: () => Promise<void>;
}) {
  const aliasProperty = findProperty(detail.properties, "alias");
  const privateProperty = findProperty(detail.properties, "private_flag");
  const [alias, setAlias] = useState(
    typeof aliasProperty?.value === "string" ? aliasProperty.value : "",
  );
  const [privateFlag, setPrivateFlag] = useState(Boolean(privateProperty?.value));
  const [evidenceIds, setEvidenceIds] = useState<readonly string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      // **两个键都写**：表单展示的是当前值，用户按保存就是确认这一份完整的地点属性。
      await Promise.all([
        updateEntityProperty(detail.entity.id, "alias", alias, [...evidenceIds]),
        updateEntityProperty(detail.entity.id, "private_flag", privateFlag, [...evidenceIds]),
      ]);
      await onSaved();
      setSaved(true);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="entity-property-form" onSubmit={submit}>
      <label>
        自定义别名
        <input
          value={alias}
          onChange={(event) => setAlias(event.target.value)}
          placeholder="例如：我们的老地方"
        />
      </label>
      <label className="entity-property-toggle">
        <input
          type="checkbox"
          checked={privateFlag}
          onChange={(event) => setPrivateFlag(event.target.checked)}
        />
        在普通页面和问答中隐藏精确地点
      </label>
      <fieldset className="entity-evidence-picker">
        <legend>支撑本次修正的原始证据（可选）</legend>
        {detail.observations.length === 0 ? (
          <small>当前没有可选 Observation。</small>
        ) : (
          detail.observations.map((observation, index) => (
            <label key={observation.id}>
              <input
                type="checkbox"
                checked={evidenceIds.includes(observation.id)}
                onChange={(event) => {
                  setEvidenceIds((current) =>
                    event.target.checked
                      ? [...current, observation.id]
                      : current.filter((id) => id !== observation.id),
                  );
                }}
              />
              原始证据 {index + 1}
            </label>
          ))
        )}
      </fieldset>
      <div className="entity-property-actions">
        <button type="submit" className="button primary small" disabled={busy}>
          {busy ? "保存中…" : "保存地点属性"}
        </button>
        {saved ? <small className="entity-saved">已保存</small> : null}
        {error !== null ? <small className="error">{error}</small> : null}
      </div>
    </form>
  );
}

export interface EntityDetailViewProps {
  detail: EntityDetailFullResponse;
  onClose: () => void;
}

export function EntityDetailView({ detail: initial, onClose }: EntityDetailViewProps) {
  const [detail, setDetail] = useState(initial);
  const [asset, setAsset] = useState<{
    asset: AssetDetailResponse;
    observations: ObservationPayload[];
  } | null>(null);

  const reload = async () => {
    setDetail(await getEntity(detail.entity.id));
  };

  function openObservation(observation: ObservationPayload) {
    setAsset({
      asset: assetFromObservation(observation),
      observations: detail.observations.filter(
        (item) => item.asset_id === observation.asset_id,
      ),
    });
  }

  return (
    <div className="entity-dialog" role="dialog" aria-modal="true" aria-label="实体详情">
      <button type="button" className="entity-close" onClick={onClose}>×</button>
      <div className="entity-dialog-panel">
        <p className="section-kicker">ENTITY · {detail.entity.entity_type}</p>
        <h2>{detail.entity.canonical_name}</h2>
        {detail.entity.entity_type !== 'person' && <MemoryCorrection kind="entity" targetId={detail.entity.id} onSaved={reload} />}
        <CorrectionHistory scopeId={detail.entity.scope_id} />
        <p className="entity-lead">
          {detail.entity.summary || "这是跨多个事件维护的实体，不是单张图片的描述。"}
        </p>
        <div className="entity-facts">
          <span>类型 · {detail.entity.entity_type}</span>
          <span>原始证据 · {detail.observations.length}</span>
          <span>属性版本 · {detail.property_history.length}</span>
          <span>置信度 · {Math.round(detail.entity.confidence * 100)}%</span>
        </div>

        <section className="entity-section">
          <h3>当前属性</h3>
          {detail.properties.length === 0 ? (
            <p className="entity-empty">暂无维护属性。模型或用户修正后，会在这里显示当前版本和可回溯证据。</p>
          ) : (
            <ul className="entity-property-list">
              {detail.properties.map((property) => (
                <li key={property.id}>
                  <strong>{property.property_key} · {propertyValue(property)}</strong>
                  <small>
                    {property.source} · {property.status} · 置信度{" "}
                    {Math.round(property.confidence * 100)}% · v{property.revision}
                  </small>
                </li>
              ))}
            </ul>
          )}
          {detail.entity.entity_type === "place" ? (
            <PlacePropertyEditor detail={detail} onSaved={reload} />
          ) : null}
        </section>

        <section className="entity-section">
          <h3>
            关联事件与关系 · {detail.relationships.length} 条关系 · {detail.facts.length} 条事实
          </h3>
          {detail.relationships.length === 0 && detail.facts.length === 0 ? (
            <p className="entity-empty">暂无关系和事实。</p>
          ) : (
            <ul className="entity-relation-list">
              {detail.relationships.map((relationship) => (
                <li key={relationship.id}>
                  <strong>
                    {relationship.subject_name} {relationship.predicate} {relationship.object_name}
                  </strong>
                  <small>{relationship.status}</small>
                </li>
              ))}
              {detail.facts.map((fact) => (
                <li key={fact.id}>
                  <strong>{fact.subject} {fact.predicate} {fact.object}</strong>
                  <small>{fact.status}</small>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="entity-section">
          <h3>原始证据 · {detail.observations.length} 条观察</h3>
          {detail.observations.length === 0 ? (
            <p className="entity-empty">该实体尚未关联可查看的 Observation。</p>
          ) : (
            <ul className="entity-observation-list">
              {detail.observations.map((observation) => (
                <li key={observation.id}>
                  <button type="button" onClick={() => openObservation(observation)}>
                    <strong>{observation.caption || "原始观察证据"}</strong>
                    <small>
                      {observation.captured_at ?? "未标注时间"} · {observation.source_type}
                    </small>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="entity-section">
          <h3>关联事件 · {detail.events.length} 个</h3>
          {detail.events.length === 0 ? (
            <p className="entity-empty">暂无关联事件。</p>
          ) : (
            <ul className="entity-event-list">
              {detail.events.map((event) => (
                <li key={event.id}>
                  <strong>{event.title}</strong>
                  <small>
                    {event.time_start ?? "未标注时间"} · {event.place || "未标注地点"}
                  </small>
                  {event.activity || event.summary ? (
                    <p>{event.activity || event.summary}</p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </section>

        <p className="entity-source-note">证据文件：{detail.evidence_files.length} 个</p>
      </div>

      {asset !== null ? (
        <AssetDetail
          asset={asset.asset}
          observations={asset.observations}
          onClose={() => setAsset(null)}
        />
      ) : null}
    </div>
  );
}
