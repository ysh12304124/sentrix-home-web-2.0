import { useId, useRef, useState } from "react";

import { deliveredPhotoThumbUrl, type FamilyPerson } from "../../../shared/api/client";
import { Modal } from "../../../shared/ui/Modal";
import { describeReviewError } from "../reviewErrors";
import { isPendingPerson, personDisplayName } from "./membership";
import { PersonAvatar } from "./PersonAvatar";
import { namesakeMatchesAlias } from "./sameName";

interface Props {
  readonly person: FamilyPerson;
  readonly candidates: readonly FamilyPerson[];
  readonly name: string;
  readonly familyRole: string | null;
  readonly onMerge: (candidate: FamilyPerson) => Promise<void>;
  readonly onKeepApart: () => Promise<void>;
  readonly onCancel: () => void;
}

export function SameNameDialog({ person, candidates, name, familyRole, onMerge, onKeepApart, onCancel }: Props) {
  const group = useId();
  const [candidateId, setCandidateId] = useState(candidates.length === 1 ? candidates[0].id : "");
  const [busy, setBusy] = useState(false);
  const active = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const selected = candidates.find((candidate) => candidate.id === candidateId);
  const viaAlias = selected !== undefined && namesakeMatchesAlias(selected, name);

  async function run(action: () => Promise<void>) {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError(describeReviewError(cause));
      active.current = false;
      setBusy(false);
    }
  }

  return (
    <Modal
      kicker="同名提醒"
      title={candidates.length === 1 ? `已有一位「${name}」，是同一个人吗？` : `有多位人物与「${name}」同名`}
      onClose={busy ? () => undefined : onCancel}
    >
      <p className="merge-dialog-lead">
        {candidates.length > 1
          ? "请选择与当前这位是同一个人的候选。"
          : viaAlias
            ? `「${name}」是「${personDisplayName(selected)}」的别名。`
            : `这个相册里已经有人叫「${name}」。`}
        如果是同一个人，合并后照片、关系和称呼都会归到一起。
      </p>
      <div className="merge-dialog-option">
        <PersonAvatar person={person} size="sm" />
        <span>当前这位</span>
      </div>
      <ul className="merge-dialog-list same-name-pair" aria-label="同名候选">
        {candidates.map((item) => (
          <li key={item.id}>
            <label className="merge-dialog-option">
              {candidates.length > 1 ? (
                <input type="radio" name={group} checked={item.id === candidateId}
                  disabled={busy} onChange={() => setCandidateId(item.id)} />
              ) : null}
              <PersonAvatar person={item} size="sm" />
              <span className="merge-dialog-name">
                {personDisplayName(item)}
                <small>
                  {isPendingPerson(item) ? "待确认 · " : ""}出现 {item.appearance_count} 次
                  {candidates.length > 1 ? (namesakeMatchesAlias(item, name) ? " · 别名匹配" : " · 本名匹配") : ""}
                </small>
              </span>
              {candidates.length > 1 && item.representative_media[0] ? (
                <img className="person-avatar sm" src={deliveredPhotoThumbUrl(item.representative_media[0].id)} alt="" loading="lazy" />
              ) : null}
            </label>
          </li>
        ))}
      </ul>
      {familyRole !== null && selected !== undefined && !isPendingPerson(selected) ? (
        <p>合并会沿用已保存的家庭角色。「{familyRole}」只在分开确认时保存。</p>
      ) : null}
      <p className="merge-dialog-warning">合并不会改动原始照片，但无法一键撤销。</p>
      {error === null ? null : <p className="error" role="alert">{error}</p>}
      <div className="merge-dialog-actions">
        <button type="button" className="button ghost" disabled={busy} onClick={onCancel}>取消</button>
        <button type="button" className="button ghost" disabled={busy} onClick={() => void run(onKeepApart)}>
          不是，只是同名
        </button>
        <button type="button" className="button primary" disabled={busy || selected === undefined}
          onClick={() => { if (selected !== undefined) void run(() => onMerge(selected)); }}>
          是同一个人，合并
        </button>
      </div>
    </Modal>
  );
}
