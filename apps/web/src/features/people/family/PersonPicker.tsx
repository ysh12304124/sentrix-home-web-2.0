/**
 * 选人列表：建关系选「对方」、合并选「和谁是同一个人」共用。
 *
 * 原来两处一个是只有文字的 `<select>`、一个是简易列表；一屏「未确认姓名人物」时根本
 * 认不出是谁。这里每一项带头像、名字、状态与出现次数，可按名字或别名搜索；偶遇面孔
 * （出现 1～2 次的待确认面孔与标为路人的人）默认收起，需要时再展开。
 */

import { useId, useMemo, useState } from "react";

import type { FamilyPerson } from "../../../shared/api/client";
import { isPendingPerson, personDisplayName } from "./membership";
import { PersonAvatar } from "./PersonAvatar";
import { matchPerson, personTier, sortPeople } from "./strangers";

interface Props {
  /** 候选人（调用方已排除本人、已按空间过滤）。 */
  readonly people: readonly FamilyPerson[];
  readonly selectedId: string | null;
  readonly onSelect: (person: FamilyPerson) => void;
  /** 列表的无障碍名，也用作搜索框的名字前缀。 */
  readonly label: string;
  readonly placeholder?: string;
  readonly disabled?: boolean;
}

export function PersonPicker({
  people,
  selectedId,
  onSelect,
  label,
  placeholder = "搜名字或别名…",
  disabled = false,
}: Props) {
  const [query, setQuery] = useState("");
  const [showPassersby, setShowPassersby] = useState(false);
  const listId = useId();

  const { visible, hiddenCount } = useMemo(() => {
    const matched = sortPeople(people)
      .map((person) => ({ person, alias: matchPerson(person, query) }))
      .filter((item) => item.alias !== undefined);
    // 已选中的人即使属于偶遇面孔也要显示，否则用户看不到自己选了谁（例如图上拖拽预选）。
    const shown = matched.filter(
      (item) =>
        showPassersby || personTier(item.person) !== "passerby" || item.person.id === selectedId,
    );
    return { visible: shown, hiddenCount: matched.length - shown.length };
  }, [people, query, showPassersby, selectedId]);

  return (
    <div className="person-picker">
      <input
        type="search"
        aria-label={`搜索${label}`}
        aria-controls={listId}
        placeholder={placeholder}
        value={query}
        disabled={disabled}
        onChange={(event) => setQuery(event.target.value)}
      />
      {visible.length === 0 ? (
        <p className="detail-empty">{hiddenCount > 0 ? "常见人物里没有匹配的人。" : "没有匹配的人物。"}</p>
      ) : (
        <ul id={listId} className="person-picker-list" role="group" aria-label={label}>
          {visible.map(({ person, alias }) => {
            const selected = person.id === selectedId;
            return (
              <li key={person.id}>
                <button
                  type="button"
                  className={selected ? "person-picker-item selected" : "person-picker-item"}
                  aria-pressed={selected}
                  disabled={disabled}
                  data-option-id={person.id}
                  onClick={() => onSelect(person)}
                >
                  <PersonAvatar person={person} size="sm" />
                  <span className="person-picker-name">
                    {personDisplayName(person)}
                    {alias === null || alias === undefined ? null : (
                      <small className="person-picker-alias">别名：{alias}</small>
                    )}
                  </span>
                  <span className="person-picker-tags">
                    {person.hidden ? <span className="person-tag passerby">路人</span> : null}
                    {isPendingPerson(person) ? <span className="person-tag pending">待确认</span> : null}
                    <small>出现 {person.appearance_count} 次</small>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {hiddenCount === 0 && !showPassersby ? null : (
        <button
          type="button"
          className="text-button"
          onClick={() => setShowPassersby((current) => !current)}
        >
          {showPassersby ? "收起偶遇面孔" : `显示偶遇面孔（${hiddenCount}）`}
        </button>
      )}
    </div>
  );
}
