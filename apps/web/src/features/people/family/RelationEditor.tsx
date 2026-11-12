/**
 * 人物详情里的「关系」一段：已确认关系的列表 + 建立关系的表单。
 *
 * **关系本来就是双向的**：后端一次写正反两行（`set_relationship`），所以 A 和 B 的档案
 * 都能读到同一条关系。此前详情显示的是「我自己的称谓」而不是「对方是我的什么人」，
 * 在 B 的档案里 A 显示成「儿子」，读起来像关系只写了一边。这里每条关系都写成整句：
 * 「A 是 B 的父亲」。
 *
 * 建关系也改成整句：「[对方] 是 [我] 的 [称谓]」，反向称谓自动推出，推不出来（父亲 →
 * 儿子还是女儿）才多问一句，提交前给出将要记录的两句话。
 */

import { useId, useState, type FormEvent } from "react";

import {
  deleteFamilyRelationship,
  putFamilyRelationship,
  type FamilyPerson,
  type FamilyRelationship,
} from "../../../shared/api/client";
import { confirmedRelationships } from "./graphLayout";
import {
  myRoleOptions,
  personDisplayName,
  relationFromSelf,
  RELATION_ROLE_GROUPS,
} from "./membership";
import { PersonAvatar } from "./PersonAvatar";
import { PersonPicker } from "./PersonPicker";

interface Props {
  readonly person: FamilyPerson;
  readonly people: readonly FamilyPerson[];
  readonly relationships: readonly FamilyRelationship[];
  readonly busy: boolean;
  /** 详情的统一写入口：管 busy / 错误提示，成功后重拉图谱。 */
  readonly run: (action: () => Promise<unknown>) => Promise<boolean>;
  /** 图上拖拽落到人身上时预选的对方。 */
  readonly relateTarget?: string | null;
}

export function RelationEditor({ person, people, relationships, busy, run, relateTarget = null }: Props) {
  const [targetId, setTargetId] = useState<string | null>(relateTarget);
  const [role, setRole] = useState("");
  const [myRole, setMyRole] = useState("");

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
  const existing = target === undefined ? undefined : edges.find((item) => item.otherId === target.id);

  const options = myRoleOptions(role);
  const effectiveMyRole = options.length === 1 ? options[0] : options.includes(myRole) ? myRole : "";
  const ready = target !== undefined && role !== "" && effectiveMyRole !== "";

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || target === undefined) return;
    // 「对方是我的 role」= subject 对方 · predicate role · object 我；反向即「我是对方的 myRole」。
    void run(() =>
      putFamilyRelationship({
        scopeId: person.scope_id,
        subjectEntityId: target.id,
        predicate: role,
        objectEntityId: person.id,
        inversePredicate: effectiveMyRole,
      }),
    ).then((ok) => {
      if (!ok) return;
      setRole("");
      setMyRole("");
      setTargetId(null);
    });
  }

  return (
    <section className="detail-section">
      <h3>关系</h3>
      {edges.length === 0 ? (
        <p className="detail-empty">当前无已确认关系。</p>
      ) : (
        <ul className="detail-relations">
          {edges.map(({ edge, otherId, otherRole }) => {
            const other = people.find((item) => item.id === otherId);
            return (
              <li key={`${edge.subject_entity_id}-${edge.object_entity_id}`}>
                {other === undefined ? null : <PersonAvatar person={other} size="sm" />}
                <span className="relation-sentence">
                  <b>{nameOf(otherId)}</b> 是 {selfName} 的<b className="relation-role">{otherRole}</b>
                </span>
                <button
                  type="button"
                  className="text-button"
                  disabled={busy}
                  aria-label={`删除关系：${nameOf(otherId)} 是 ${selfName} 的${otherRole}`}
                  onClick={() =>
                    void run(() =>
                      deleteFamilyRelationship({
                        scopeId: person.scope_id,
                        subjectEntityId: edge.subject_entity_id,
                        objectEntityId: edge.object_entity_id,
                      }),
                    )
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
            disabled={busy}
          />
          <RelationRoleFields
            otherName={target === undefined ? "对方" : personDisplayName(target)}
            selfName={selfName}
            role={role}
            myRole={effectiveMyRole}
            onRole={(next) => {
              setRole(next);
              setMyRole("");
            }}
            onMyRole={setMyRole}
            disabled={busy}
          />
          {existing === undefined ? null : (
            <p className="detail-warning">
              将替换现有关系：{personDisplayName(target as FamilyPerson)} 是 {selfName} 的{existing.otherRole}
            </p>
          )}
          <button type="submit" className="button primary small" disabled={busy || !ready}>
            建立关系
          </button>
        </form>
      )}
    </section>
  );
}

/**
 * 一句话选称谓：「{other} 是 {self} 的 [称谓]」+ 必要时「{self} 是 {other} 的 [儿子｜女儿]」+ 预览。
 * 详情建关系与图谱改关系共用，保证两处说的是同一种话。
 */
export function RelationRoleFields({
  otherName,
  selfName,
  role,
  myRole,
  onRole,
  onMyRole,
  disabled = false,
}: {
  otherName: string;
  selfName: string;
  role: string;
  /** 已生效的反向称谓：只有一个可选项时由调用方直接填上。 */
  myRole: string;
  onRole: (role: string) => void;
  onMyRole: (role: string) => void;
  disabled?: boolean;
}) {
  const options = myRoleOptions(role);
  // 同一屏可能同时有详情与图谱两份表单，单选组的 name 必须各自独立。
  const radioName = useId();
  return (
    <div className="relation-fields">
      <label className="relation-line">
        <span>
          <b>{otherName}</b> 是 {selfName} 的
        </span>
        <select
          aria-label="关系"
          value={role}
          disabled={disabled}
          onChange={(event) => onRole(event.target.value)}
        >
          <option value="">选择称谓…</option>
          {RELATION_ROLE_GROUPS.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {group.roles.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
      {options.length > 1 ? (
        <div className="relation-line" role="radiogroup" aria-label={`${selfName} 是 ${otherName} 的`}>
          <span>
            {selfName} 是 <b>{otherName}</b> 的
          </span>
          {options.map((item) => (
            <label key={item} className={myRole === item ? "relation-choice active" : "relation-choice"}>
              <input
                type="radio"
                name={radioName}
                value={item}
                checked={myRole === item}
                disabled={disabled}
                onChange={() => onMyRole(item)}
              />
              {item}
            </label>
          ))}
        </div>
      ) : null}
      {role !== "" && myRole !== "" ? (
        <p className="relation-preview" aria-live="polite">
          将记录：{otherName} 是 {selfName} 的{role}；{selfName} 是 {otherName} 的{myRole}
        </p>
      ) : null}
    </div>
  );
}
