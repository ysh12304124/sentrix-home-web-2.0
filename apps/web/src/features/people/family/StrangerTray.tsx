/**
 * 名录底部的「偶遇面孔」区：只出现 1～2 次的待确认面孔，加上用户标为路人的人。
 *
 * 默认收起（搜索或筛选时由名录强制展开）。展开后是一格小头像，点一下照常进详情；
 * 「批量整理」进入多选，可以一次：
 *
 * - **标为路人 / 取消路人标记**：只是不再铺开，人脸与照片都留着；
 * - **这不是人物**：拒绝这些人的人脸分组（与详情里同名按钮同一条路），原始照片不受影响，
 *   已确认的人后端会拒绝，所以只对待确认的人生效；
 * - **合并为同一人**：交给名录的合并确认框。
 */

import { useState } from "react";

import {
  getPerson,
  rejectFaceCluster,
  setPersonHidden,
  type FamilyPerson,
} from "../../../shared/api/client";
import { describeReviewError } from "../reviewErrors";
import { isPendingPerson, personDisplayName } from "./membership";
import { PersonAvatar } from "./PersonAvatar";

interface Props {
  readonly people: readonly FamilyPerson[];
  /** 搜索或筛选时强制展开：搜到了却藏在折叠区里等于没搜到。 */
  readonly forceOpen: boolean;
  readonly selectedId: string | null;
  readonly onSelect: (personId: string) => void;
  /** 名录的「合并人物」模式开着时，点脸是勾选而不是进详情。 */
  readonly merging: boolean;
  readonly isPicked: (personId: string) => boolean;
  readonly onPick: (personId: string) => void;
  readonly onMerge: (people: FamilyPerson[]) => void;
  readonly onChanged?: () => Promise<void>;
}

export function StrangerTray({
  people,
  forceOpen,
  selectedId,
  onSelect,
  merging,
  isPicked,
  onPick,
  onMerge,
  onChanged,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [confirmReject, setConfirmReject] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (people.length === 0) return null;

  const chosenPeople = people.filter((person) => chosen.includes(person.id));
  const rejectable = chosenPeople.filter(isPendingPerson);
  const hiddenChosen = chosenPeople.filter((person) => person.hidden);
  const visibleChosen = chosenPeople.filter((person) => !person.hidden);
  const mixedScopes = new Set(chosenPeople.map((person) => person.scope_id)).size > 1;
  const canBatch = onChanged !== undefined;

  function stopSelecting() {
    setSelecting(false);
    setChosen([]);
    setConfirmReject(false);
  }

  function toggle(personId: string) {
    setChosen((current) =>
      current.includes(personId) ? current.filter((id) => id !== personId) : [...current, personId],
    );
  }

  async function apply(action: () => Promise<string>) {
    if (onChanged === undefined) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const message = await action();
      await onChanged();
      stopSelecting();
      setNotice(message);
    } catch (cause) {
      setError(describeReviewError(cause));
    } finally {
      setBusy(false);
    }
  }

  function setHidden(targets: readonly FamilyPerson[], hidden: boolean) {
    void apply(async () => {
      // 逐个写：每一条都是一次独立的属性修订，失败时已写的那些保持生效。
      for (const person of targets) await setPersonHidden(person.id, hidden);
      return hidden ? `已将 ${targets.length} 人标为路人` : `已取消 ${targets.length} 人的路人标记`;
    });
  }

  function rejectChosen() {
    void apply(async () => {
      let removed = 0;
      for (const person of rejectable) {
        const detail = await getPerson(person.id);
        const cluster = detail.clusters.find((item) => item.status !== "rejected");
        if (cluster === undefined) continue;
        await rejectFaceCluster(cluster.id);
        removed += 1;
      }
      const skipped = chosenPeople.length - removed;
      return skipped > 0 ? `已移除 ${removed} 组面孔，${skipped} 人未移除（已确认或没有人脸分组）` : `已移除 ${removed} 组面孔`;
    });
  }

  const open = forceOpen || expanded;

  return (
    <section className="stranger-tray" aria-label="偶遇面孔">
      <details open={open} onToggle={(event) => setExpanded(event.currentTarget.open)}>
        <summary>
          偶遇面孔 · {people.length}
          <small>只出现 1～2 次、或被标为路人的面孔{open ? "" : "，展开查看"}</small>
        </summary>

        {canBatch && !merging ? (
          <div className="stranger-toolbar" role="toolbar" aria-label="批量整理偶遇面孔">
            {selecting ? (
              <>
                <span>已选 {chosen.length} 人</span>
                <button
                  type="button"
                  className="text-button"
                  disabled={busy}
                  onClick={() =>
                    setChosen(chosen.length === people.length ? [] : people.map((person) => person.id))
                  }
                >
                  {chosen.length === people.length ? "全不选" : "全选"}
                </button>
                <button
                  type="button"
                  className="button ghost small"
                  disabled={busy || visibleChosen.length === 0}
                  onClick={() => setHidden(visibleChosen, true)}
                >
                  标为路人
                </button>
                {hiddenChosen.length === 0 ? null : (
                  <button
                    type="button"
                    className="button ghost small"
                    disabled={busy}
                    onClick={() => setHidden(hiddenChosen, false)}
                  >
                    取消路人标记
                  </button>
                )}
                <button
                  type="button"
                  className="button ghost small"
                  disabled={busy || rejectable.length === 0}
                  onClick={() => setConfirmReject(true)}
                >
                  这不是人物
                </button>
                <button
                  type="button"
                  className="button ghost small"
                  disabled={busy || chosenPeople.length < 2 || mixedScopes}
                  title={mixedScopes ? "只能合并同一相册里的人物" : undefined}
                  onClick={() => {
                    onMerge(chosenPeople);
                    stopSelecting();
                  }}
                >
                  合并为同一人
                </button>
                <button type="button" className="button ghost small" disabled={busy} onClick={stopSelecting}>
                  完成
                </button>
              </>
            ) : (
              <button
                type="button"
                className="button ghost small"
                onClick={() => {
                  setSelecting(true);
                  setNotice(null);
                }}
              >
                批量整理
              </button>
            )}
          </div>
        ) : null}

        {confirmReject ? (
          <div className="detail-confirm-row" role="group" aria-label="确认移除面孔">
            <span>
              移除 {rejectable.length} 组面孔？它们不再作为人物出现，原始照片不受影响。
            </span>
            <button type="button" className="button ghost small" disabled={busy} onClick={rejectChosen}>
              确定移除
            </button>
            <button
              type="button"
              className="button ghost small"
              disabled={busy}
              onClick={() => setConfirmReject(false)}
            >
              取消
            </button>
          </div>
        ) : null}

        {error === null ? null : (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {notice === null ? null : (
          <p className="stranger-notice" role="status">
            {notice}
          </p>
        )}

        <ul className="stranger-grid">
          {people.map((person) => {
            const checked = merging ? isPicked(person.id) : selecting ? chosen.includes(person.id) : false;
            const classes = ["stranger-face"];
            if (checked) classes.push("checked");
            if (!merging && !selecting && person.id === selectedId) classes.push("selected");
            return (
              <li key={person.id}>
                <button
                  type="button"
                  className={classes.join(" ")}
                  data-person-id={person.id}
                  aria-pressed={merging || selecting ? checked : person.id === selectedId}
                  disabled={busy}
                  title={`${personDisplayName(person)} · 出现 ${person.appearance_count} 次`}
                  onClick={() => {
                    if (merging) onPick(person.id);
                    else if (selecting) toggle(person.id);
                    else onSelect(person.id);
                  }}
                >
                  {merging || selecting ? (
                    <span className="person-card-check" aria-hidden="true">
                      {checked ? "✓" : ""}
                    </span>
                  ) : null}
                  <PersonAvatar person={person} size="sm" />
                  <small>
                    {person.hidden ? "路人 · " : ""}
                    {person.appearance_count} 次
                  </small>
                </button>
              </li>
            );
          })}
        </ul>
      </details>
    </section>
  );
}
