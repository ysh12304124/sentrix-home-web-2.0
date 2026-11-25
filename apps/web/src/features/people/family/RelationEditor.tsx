/**
 * 人物详情里的「关系」一段：已确认关系的列表 + 建立关系的表单。
 *
 * **关系本来就是双向的**：后端一次写正反两行（`set_relationship`），所以 A 和 B 的档案
 * 都能读到同一条关系。此前详情显示的是「我自己的称谓」而不是「对方是我的什么人」，
 * 在 B 的档案里 A 显示成「儿子」，读起来像关系只写了一边。这里每条关系都写成整句：
 * 「A 是 B 的父亲」。
 *
 * 建关系也是整句：「[对方] 是 [我] 的 [称谓]」。**称谓由用户自己写**（2026-11-12 起不再
 * 从词表里选）：输入框给出建议（本相册用过的称谓在前、再是常用称谓），反向称谓按亲属表
 * 自动填上、有几种可能时给出点选，不认识的称谓默认同一个词；两句都能改，提交前预览。
 */

import { useId, useMemo, useState, type FormEvent, type ReactNode } from "react";

import {
  deleteFamilyRelationship,
  putFamilyRelationship,
  type FamilyPerson,
  type FamilyRelationship,
} from "../../../shared/api/client";
import { confirmedRelationships, dedupeRelationships } from "./graphLayout";
import {
  myRoleOptions,
  personDisplayName,
  relationFromSelf,
  relationLabelError,
  relationLabelSuggestions,
  suggestReverse,
} from "./membership";
import { PersonAvatar } from "./PersonAvatar";
import { PersonPicker } from "./PersonPicker";
import { relationshipWriteKey, usePeopleWrites } from "./usePeopleWrites";

interface Props {
  readonly person: FamilyPerson;
  readonly people: readonly FamilyPerson[];
  readonly relationships: readonly FamilyRelationship[];
  readonly busy: boolean;
  /** 详情的写入口：管错误提示与同步进度。关系写入不锁整个侧栏，只锁这一对人。 */
  readonly run: (action: () => Promise<unknown>) => Promise<boolean>;
  /** 写回执先落本地：这对人之间写完后生效的行（删除时为空）。 */
  readonly onRelationsChanged?: (scopeId: string, a: string, b: string, rows: readonly FamilyRelationship[]) => void;
  /** 图上拖拽落到人身上时预选的对方。 */
  readonly relateTarget?: string | null;
  /** 排在关系列表之前的内容（详情把「家庭归属」放在这里：它也是「他和这个家的关系」）。 */
  readonly header?: ReactNode;
}

export function RelationEditor({
  person,
  people,
  relationships,
  busy,
  run,
  onRelationsChanged,
  relateTarget = null,
  header = null,
}: Props) {
  const [targetId, setTargetId] = useState<string | null>(relateTarget);
  const labels = useRelationLabels();
  const { pending, write } = usePeopleWrites();
  const suggestions = useMemo(
    () => relationLabelSuggestions(confirmedRelationships(relationships).flatMap((edge) => [edge.predicate, edge.inverse_predicate])),
    [relationships],
  );

  // 只能和同一记忆空间的人建关系；跨空间的拖拽预选在这里被过滤掉，提交按钮因此不可用。
  const others = people.filter((item) => item.id !== person.id && item.scope_id === person.scope_id);
  const target = others.find((item) => item.id === targetId);
  const selfName = personDisplayName(person);
  const nameOf = (id: string) => {
    const found = people.find((item) => item.id === id);
    return found === undefined ? "未知人物" : personDisplayName(found);
  };

  const edges = confirmedRelationships(relationships)
    .filter((edge) => edge.subject_entity_id === person.id || edge.object_entity_id === person.id)
    .map((edge) => ({ edge, ...relationFromSelf(edge, person.id) }));
  const existing = target === undefined ? undefined : dedupeRelationships(relationships.filter((edge) =>
    edge.scope_id === person.scope_id &&
    ((edge.subject_entity_id === person.id && edge.object_entity_id === target.id)
    || (edge.object_entity_id === person.id && edge.subject_entity_id === target.id)),
  ))[0];
  const existingRole = existing === undefined ? null : relationFromSelf(existing, person.id).otherRole;
  const targetKey = target === undefined ? null : relationshipWriteKey(person.scope_id, person.id, target.id);
  const saving = busy || (targetKey !== null && pending.has(targetKey));
  const ready = target !== undefined && labels.ready;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (saving || !ready || target === undefined || targetKey === null) return;
    // 「对方是我的 role」：subject 对方，object 我。
    void write(targetKey, async () => {
      const ok = await run(async () => {
        const result = await putFamilyRelationship({
          scopeId: person.scope_id,
          subjectEntityId: target.id,
          predicate: labels.cleanRole,
          objectEntityId: person.id,
          inversePredicate: labels.cleanReverse,
        });
        onRelationsChanged?.(person.scope_id, target.id, person.id, result.relationships);
        return result;
      });
      if (ok) {
        labels.reset();
        setTargetId(null);
      }
    });
  }

  return (
    <section className="detail-section">
      <h3>关系</h3>
      {header}
      {edges.length === 0 ? (
        <p className="detail-empty">当前无已确认关系。</p>
      ) : (
        <ul className="detail-relations">
          {edges.map(({ edge, otherId, otherRole }) => {
            const other = people.find((item) => item.id === otherId);
            const key = relationshipWriteKey(person.scope_id, edge.subject_entity_id, edge.object_entity_id);
            return (
              <li key={`${edge.subject_entity_id}-${edge.object_entity_id}`}>
                {other === undefined ? null : <PersonAvatar person={other} size="sm" />}
                <span className="relation-sentence">
                  <b>{nameOf(otherId)}</b> 是 {selfName} 的<b className="relation-role">{otherRole}</b>
                </span>
                <button
                  type="button"
                  className="text-button"
                  disabled={busy || pending.has(key)}
                  aria-label={`删除关系：${nameOf(otherId)} 是 ${selfName} 的${otherRole}`}
                  onClick={() =>
                    void write(key, async () => {
                      await run(async () => {
                        const result = await deleteFamilyRelationship({
                          scopeId: person.scope_id,
                          subjectEntityId: edge.subject_entity_id,
                          objectEntityId: edge.object_entity_id,
                        });
                        onRelationsChanged?.(person.scope_id, edge.subject_entity_id, edge.object_entity_id, []);
                        return result;
                      });
                    })
                  }
                >
                  删除
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {others.length === 0 ? (
        <p className="detail-empty">当前记忆空间暂无其他人物可建立关系。</p>
      ) : (
        <form className="detail-form relation-form" onSubmit={submit}>
          <h4>建立关系</h4>
          <p className="detail-empty">选择对方（仅显示同一记忆空间的人物）</p>
          <PersonPicker
            people={others}
            selectedId={target?.id ?? null}
            onSelect={(item) => setTargetId(item.id)}
            label="对方"
            disabled={saving}
          />
          <RelationLabelFields
            labels={labels}
            otherName={target === undefined ? "对方" : personDisplayName(target)}
            selfName={selfName}
            suggestions={suggestions}
            disabled={saving}
          />
          {existingRole === null || target === undefined ? null : (
            <p className="detail-warning">
              将替换现有关系：{personDisplayName(target)} 是 {selfName} 的{existingRole}
              {edges.some((edge) => edge.otherId === target.id) ? "" : "（系统推断）"}
            </p>
          )}
          <button type="submit" className="button primary small" disabled={saving || !ready}>
            {existingRole === null ? "建立关系" : "替换关系"}
          </button>
        </form>
      )}
    </section>
  );
}

export interface RelationLabels {
  readonly role: string;
  readonly reverse: string;
  readonly cleanRole: string;
  readonly cleanReverse: string;
  readonly ready: boolean;
  readonly error: string | null;
  readonly setRole: (next: string) => void;
  readonly setReverse: (next: string) => void;
  readonly reset: () => void;
}

/**
 * 一对称谓的编辑状态。用户没碰过反向称谓时，它跟着正向称谓自动给默认值
 * （`suggestReverse`）；用户手动改过就不再覆盖，清空后恢复自动。
 */
export function useRelationLabels(initial?: { role: string; reverse: string }): RelationLabels {
  const [role, setRoleState] = useState(initial?.role ?? "");
  const [reverse, setReverseState] = useState(initial?.reverse ?? "");
  const [touched, setTouched] = useState(false);
  const cleanRole = role.trim();
  const cleanReverse = reverse.trim();
  const error = relationLabelError(cleanRole) ?? relationLabelError(cleanReverse);
  return {
    role,
    reverse,
    cleanRole,
    cleanReverse,
    error,
    ready: cleanRole !== "" && cleanReverse !== "" && error === null,
    setRole(next) {
      setRoleState(next);
      if (!touched) setReverseState(suggestReverse(next));
    },
    setReverse(next) {
      setReverseState(next);
      setTouched(next.trim() !== "");
    },
    reset() {
      setRoleState("");
      setReverseState("");
      setTouched(false);
    },
  };
}

/**
 * 一句话写称谓：「{other} 是 {self} 的 [____]」+「{self} 是 {other} 的 [____]」+ 预览。
 * 详情建关系与图谱改关系共用，保证两处说的是同一种话。
 */
export function RelationLabelFields({
  labels,
  otherName,
  selfName,
  suggestions,
  disabled = false,
}: {
  labels: RelationLabels;
  otherName: string;
  selfName: string;
  suggestions: readonly string[];
  disabled?: boolean;
}) {
  // 同一屏可能同时有详情与图谱两份表单，datalist 的 id 必须各自独立。
  const listId = useId();
  const options = myRoleOptions(labels.cleanRole);
  return (
    <div className="relation-fields">
      <label className="relation-line">
        <span>
          <b>{otherName}</b> 是 {selfName} 的
        </span>
        <input
          aria-label="关系称谓"
          list={listId}
          value={labels.role}
          placeholder="自己写，如 爸爸、同事、干妈"
          disabled={disabled}
          onChange={(event) => labels.setRole(event.target.value)}
        />
      </label>
      <datalist id={listId}>
        {suggestions.map((item) => (
          <option key={item} value={item} />
        ))}
      </datalist>
      <label className="relation-line">
        <span>
          {selfName} 是 <b>{otherName}</b> 的
        </span>
        <input
          aria-label="反向称谓"
          value={labels.reverse}
          placeholder="反过来怎么称呼"
          disabled={disabled}
          onChange={(event) => labels.setReverse(event.target.value)}
        />
      </label>
      {options.length > 1 ? (
        <div className="relation-line relation-quick" role="group" aria-label="反向称谓建议">
          <span>可选：</span>
          {options.map((item) => (
            <button
              key={item}
              type="button"
              className={labels.cleanReverse === item ? "relation-choice active" : "relation-choice"}
              aria-pressed={labels.cleanReverse === item}
              disabled={disabled}
              onClick={() => labels.setReverse(item)}
            >
              {item}
            </button>
          ))}
        </div>
      ) : null}
      {labels.error === null ? null : (
        <p className="error" role="alert">
          {labels.error}
        </p>
      )}
      {labels.ready ? (
        <p className="relation-preview" aria-live="polite">
          将记录：{otherName} 是 {selfName} 的{labels.cleanRole}；{selfName} 是 {otherName} 的{labels.cleanReverse}
        </p>
      ) : null}
    </div>
  );
}
