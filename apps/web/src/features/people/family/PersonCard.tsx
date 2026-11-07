/**
 * 一张人物卡。整张卡是一个按钮——点哪都进详情，不需要用户先去瞄准头像或名字。
 *
 * `data-person-id` 是给测试用的稳定钩子：冒烟用例要数「8 个人渲染出 8 张卡」，
 * 靠文案数会被改名/占位串影响，靠结构数才对得上真实形状（计划书 §5）。
 */

import type { FamilyPerson } from "../../../shared/api/client";
import { PersonAvatar } from "./PersonAvatar";
import { membershipLabel, membershipOf, personDisplayName } from "./membership";

interface Props {
  readonly person: FamilyPerson;
  readonly selected: boolean;
  readonly onSelect: (personId: string) => void;
}

export function PersonCard({ person, selected, onSelect }: Props) {
  const membership = membershipOf(person);
  return (
    <li className="person-card-item">
      <button
        type="button"
        className={selected ? "person-card selected" : "person-card"}
        data-person-id={person.id}
        aria-pressed={selected}
        onClick={() => onSelect(person.id)}
      >
        <PersonAvatar person={person} />
        <span className="person-card-name">{personDisplayName(person)}</span>
        <span className={`membership-badge ${membership}`}>{membershipLabel(membership)}</span>
        <small className="person-card-meta">
          出现 {person.appearance_count} 次
          {person.portrait === null ? "" : " · 有介绍"}
        </small>
      </button>
    </li>
  );
}
