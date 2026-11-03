/**
 * 批量确认 + 批量建关系（1.0 `app.js:933-940` / `:1740-1768`）。
 *
 * **确认与建关系是两次请求**：`POST /people/batch-confirm` 逐件确认、逐件回执；
 * 关系走 `PUT /family-graph/relationships`。所以表单先收「哪些簇、叫什么名字」，
 * 再把关系行里的成员**按簇 id** 记下来——提交后由父组件用批量回执里的
 * `entity_id` 换算成真实的人物 id。用姓名当键会在两个人同名时连错人。
 *
 * 空名字的行不提交（1.0 的同一判据：至少为一个成员填写姓名）。
 */

import { useState, type FormEvent } from "react";

import type {
  BatchConfirmItem,
  FaceClusterSummary,
} from "../../shared/api/client";
import { clusterDisplayName } from "./reviewErrors";
import { relationChoices } from "./family/membership";

/**
 * 关系行的两端用**簇对应的 person_id**（即 `cluster.entity_id`），不是簇 id：
 * 批量确认的请求与回执都以 person_id 为键，回执里再换成最终 `entity_id`（同名合并时
 * 可能与 person_id 不同）。
 */
export interface BatchRelationDraft {
  subjectPersonId: string;
  objectPersonId: string;
  predicate: string;
  inversePredicate: string;
}

interface RelationRow {
  subject: string;
  choiceIndex: number;
  object: string;
}

export interface BatchConfirmPanelProps {
  readonly clusters: FaceClusterSummary[];
  readonly busy: boolean;
  readonly onSubmit: (
    items: BatchConfirmItem[],
    relations: BatchRelationDraft[],
  ) => Promise<boolean>;
  readonly onCancel: () => void;
}

export function BatchConfirmPanel({
  clusters,
  busy,
  onSubmit,
  onCancel,
}: BatchConfirmPanelProps) {
  const [names, setNames] = useState<Record<string, string>>({});
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [relations, setRelations] = useState<RelationRow[]>([
    { subject: "", choiceIndex: -1, object: "" },
  ]);
  const [error, setError] = useState<string | null>(null);

  const choices = relationChoices();
  const confirmable = clusters.filter((cluster) => cluster.entity_id !== null);
  const skipped = clusters.length - confirmable.length;

  function updateRelation(index: number, patch: Partial<RelationRow>) {
    setRelations((current) =>
      current.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)),
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const items: BatchConfirmItem[] = [];
    for (const cluster of confirmable) {
      const name = (names[cluster.id] ?? "").trim();
      if (name === "") continue;
      items.push({
        person_id: cluster.entity_id ?? "",
        name,
        family_role: (roles[cluster.id] ?? "").trim() || null,
      });
    }
    if (items.length === 0) {
      setError("至少为一个成员填写姓名。");
      return;
    }

    const drafts: BatchRelationDraft[] = [];
    for (const row of relations) {
      const choice = row.choiceIndex >= 0 ? choices[row.choiceIndex] : undefined;
      if (row.subject === "" || row.object === "" || row.subject === row.object || choice === undefined) {
        continue;
      }
      drafts.push({
        subjectPersonId: row.subject,
        objectPersonId: row.object,
        predicate: choice.predicate,
        inversePredicate: choice.inversePredicate,
      });
    }

    setError(null);
    await onSubmit(items, drafts);
  }

  return (
    <form className="merge-bar" aria-label="批量确认人物" onSubmit={submit}>
      <h3>批量确认 {clusters.length} 位人物</h3>
      <p className="merge-hint">
        为每个成员填写姓名；留空的行不会提交。关系行是可选的，提交后会在这两个人之间
        建立正式关系。
        {skipped > 0 ? ` 有 ${skipped} 位还没有可用的身份，已跳过。` : ""}
      </p>

      <ul className="merge-choices">
        {confirmable.map((cluster, index) => (
          <li key={cluster.id}>
            <label>
              成员 {index + 1} 姓名
              <input
                aria-label={`成员 ${index + 1} 姓名`}
                value={names[cluster.id] ?? ""}
                placeholder={clusterDisplayName(cluster)}
                onChange={(event) =>
                  setNames((current) => ({ ...current, [cluster.id]: event.target.value }))
                }
              />
            </label>
            <label>
              成员 {index + 1} 家庭角色
              <select
                aria-label={`成员 ${index + 1} 家庭角色`}
                value={roles[cluster.id] ?? ""}
                onChange={(event) =>
                  setRoles((current) => ({ ...current, [cluster.id]: event.target.value }))
                }
              >
                <option value="">暂不确认</option>
                {["母亲", "父亲", "孩子", "祖父母", "其他家庭成员"].map((role) => (
                  <option key={role} value={role}>
                    {role}
                  </option>
                ))}
              </select>
            </label>
          </li>
        ))}
      </ul>

      <h4>他们之间是什么关系（可选）</h4>
      <ul className="merge-choices">
        {relations.map((row, index) => (
          <li key={index}>
            <select
              aria-label={`关系成员 A ${index + 1}`}
              value={row.subject}
              onChange={(event) => updateRelation(index, { subject: event.target.value })}
            >
              <option value="">成员 A</option>
              {confirmable.map((cluster, memberIndex) => (
                <option key={cluster.id} value={cluster.entity_id ?? ""}>
                  成员 {memberIndex + 1}
                </option>
              ))}
            </select>
            <select
              aria-label={`关系类型 ${index + 1}`}
              value={row.choiceIndex}
              onChange={(event) =>
                updateRelation(index, { choiceIndex: Number(event.target.value) })
              }
            >
              <option value={-1}>关系</option>
              {choices.map((choice, choiceIndex) => (
                <option key={choice.label} value={choiceIndex}>
                  {choice.label}
                </option>
              ))}
            </select>
            <select
              aria-label={`关系成员 B ${index + 1}`}
              value={row.object}
              onChange={(event) => updateRelation(index, { object: event.target.value })}
            >
              <option value="">成员 B</option>
              {confirmable.map((cluster, memberIndex) => (
                <option key={cluster.id} value={cluster.entity_id ?? ""}>
                  成员 {memberIndex + 1}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="text-button"
              disabled={relations.length === 1}
              onClick={() =>
                setRelations((current) => current.filter((_, rowIndex) => rowIndex !== index))
              }
            >
              移除
            </button>
          </li>
        ))}
      </ul>

      <button
        type="button"
        className="button ghost small"
        onClick={() =>
          setRelations((current) => [
            ...current,
            { subject: "", choiceIndex: -1, object: "" },
          ])
        }
      >
        添加关系
      </button>

      {error === null ? null : (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <div className="cluster-actions">
        <button type="submit" className="primary" disabled={busy}>
          批量确认并建立关系
        </button>
        <button type="button" onClick={onCancel} disabled={busy}>
          取消
        </button>
      </div>
    </form>
  );
}
