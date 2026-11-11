/**
 * 「这几位是同一个人」的确认框。名录批量勾选与详情里的「与其他人合并…」共用这一个。
 *
 * 用户只做一个决定：**保留谁**（单选）。名字、状态、别名的规则在后端
 * （`domain/identity/person_merge.py`）；这里的预览只是把同一套规则摆给用户看，
 * 真正落库的以服务端回执为准，所以合并成功后一律重拉，不在本地拼结果。
 */

import { useState } from "react";

import { mergePeople, type FamilyPerson } from "../../../shared/api/client";
import { Modal } from "../../../shared/ui/Modal";
import { describeReviewError } from "../reviewErrors";
import { PersonAvatar } from "./PersonAvatar";
import { defaultMergeTarget, isNamed, isPendingPerson, personDisplayName } from "./membership";

interface Props {
  /** 至少两个人，且同一记忆空间——调用方负责挡住不满足的情况。 */
  readonly people: readonly FamilyPerson[];
  readonly onCancel: () => void;
  /** 合并成功：参数是保留下来的那个人。调用方负责重拉与跳转。 */
  readonly onMerged: (targetId: string) => void | Promise<void>;
}

export function MergePeopleDialog({ people, onCancel, onMerged }: Props) {
  const [targetId, setTargetId] = useState(() => defaultMergeTarget(people));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const target = people.find((person) => person.id === targetId) ?? people[0];
  const sources = people.filter((person) => person.id !== target.id);
  // 预览与后端同一条规则：目标有名字用目标的，否则取第一个有名字的来源。
  const named = [target, ...sources].filter(isNamed);
  const finalName = named.length > 0 ? personDisplayName(named[0]) : null;
  const aliases = named.map(personDisplayName).filter((name) => name !== finalName);
  const confirmed = people.some((person) => !isPendingPerson(person));
  const totalAppearances = people.reduce((sum, person) => sum + person.appearance_count, 0);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await mergePeople(
        target.id,
        sources.map((person) => person.id),
      );
      await onMerged(target.id);
    } catch (cause) {
      setError(describeReviewError(cause));
      setBusy(false);
    }
  }

  return (
    <Modal kicker="合并人物" title={`这 ${people.length} 位是同一个人吗？`} onClose={busy ? () => undefined : onCancel}>
      <p className="merge-dialog-lead">选一位作为保留的人，其余人的照片、关系和称呼都会并到他名下。</p>
      <ul className="merge-dialog-list" role="radiogroup" aria-label="保留谁">
        {people.map((person) => (
          <li key={person.id}>
            <label className={person.id === target.id ? "merge-dialog-option active" : "merge-dialog-option"}>
              <input
                type="radio"
                name="merge-target"
                value={person.id}
                checked={person.id === target.id}
                disabled={busy}
                onChange={() => setTargetId(person.id)}
              />
              <PersonAvatar person={person} size="sm" />
              <span className="merge-dialog-name">
                {personDisplayName(person)}
                <small>
                  {isPendingPerson(person) ? "待确认 · " : ""}出现 {person.appearance_count} 次
                </small>
              </span>
              {person.id === target.id ? <span className="merge-dialog-keep">保留</span> : null}
            </label>
          </li>
        ))}
      </ul>

      <div className="merge-dialog-preview" aria-live="polite">
        <p>
          合并后{finalName === null ? "仍是未确认姓名人物" : <>叫「<strong>{finalName}</strong>」</>}
          ，共出现 {totalAppearances} 次{confirmed ? "，为已确认人物" : "，之后可在详情里确认身份"}。
        </p>
        {aliases.length === 0 ? null : <p>其他名字会保留为别名：{aliases.join("、")}。</p>}
        <p className="merge-dialog-warning">合并不会改动原始照片，但无法一键撤销；分错的照片需要之后单独处理。</p>
      </div>

      {error === null ? null : (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <div className="merge-dialog-actions">
        <button type="button" className="button ghost" disabled={busy} onClick={onCancel}>
          取消
        </button>
        <button type="button" className="button primary" disabled={busy} onClick={() => void submit()}>
          {busy ? "正在合并…" : "确认合并"}
        </button>
      </div>
    </Modal>
  );
}
