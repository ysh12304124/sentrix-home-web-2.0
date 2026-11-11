/**
 * 名录：搜索 + 筛选 + 头像网格。**这是 `/people` 的默认视图**。
 *
 * 默认是名录不是图谱，依据是实测：刚导入的相册跑出来的图是 8 个孤点，把孤点图当
 * 默认视图是坏设计（计划书 §0）。名录至少先把「有这些人」摆清楚。
 *
 * 筛选里的 `unknown` 与 `family`/`friend` 平级，不是兜底分组——待归类的人必须和别的
 * 人一样出现在默认列表里（筛选 `all` 时），这正是 F7 要修的那条。
 */

import { useMemo, useState } from "react";

import type { FamilyPerson, FamilyMembershipValue } from "../../../shared/api/client";
import { EmptyState } from "../../../shared/ui/EmptyState";
import { PersonCard } from "./PersonCard";
import { isNamed, membershipLabel, membershipOf, personDisplayName } from "./membership";

type Filter = "all" | FamilyMembershipValue;

const FILTERS: readonly { value: Filter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "family", label: membershipLabel("family") },
  { value: "friend", label: membershipLabel("friend") },
  { value: "unknown", label: membershipLabel("unknown") },
];

/** 排序：有名的在前 → 出现次数降序 → id 升序。**稳定**，不用 localeCompare 的随机性。 */
function sortPeople(people: readonly FamilyPerson[]): FamilyPerson[] {
  return [...people].sort((a, b) => {
    const namedA = isNamed(a) ? 0 : 1;
    const namedB = isNamed(b) ? 0 : 1;
    if (namedA !== namedB) return namedA - namedB;
    if (a.appearance_count !== b.appearance_count) return b.appearance_count - a.appearance_count;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

interface Props {
  readonly people: FamilyPerson[];
  readonly selectedId: string | null;
  readonly onSelect: (personId: string) => void;
}

export function FamilyRoster({ people, selectedId, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  const counts = useMemo(() => {
    const base: Record<Filter, number> = { all: people.length, family: 0, friend: 0, unknown: 0 };
    for (const person of people) base[membershipOf(person)] += 1;
    return base;
  }, [people]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return sortPeople(people).filter((person) => {
      if (filter !== "all" && membershipOf(person) !== filter) return false;
      if (needle === "") return true;
      return personDisplayName(person).toLowerCase().includes(needle);
    });
  }, [people, query, filter]);

  // 默认视图不主动铺开「只出现一两次」的人；搜索或筛选时全部展开，否则搜到了却藏在
  // 折叠区里等于没搜到。
  const collapsedMode = query.trim() === "" && filter === "all";
  const frequent = collapsedMode
    ? visible.filter((person) => person.appearance_count > 2)
    : visible;
  const rare = collapsedMode
    ? visible.filter((person) => person.appearance_count <= 2)
    : [];

  return (
    <section className="family-roster">
      <div className="roster-toolbar">
        <input
          type="search"
          className="roster-search"
          aria-label="搜索人物"
          placeholder="搜名字…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="roster-filters" role="group" aria-label="按归属筛选">
          {FILTERS.map((item) => (
            <button
              key={item.value}
              type="button"
              className={filter === item.value ? "roster-filter active" : "roster-filter"}
              aria-pressed={filter === item.value}
              onClick={() => setFilter(item.value)}
            >
              {item.label}
              <span className="roster-filter-count">{counts[item.value]}</span>
            </button>
          ))}
        </div>
      </div>

      {visible.length === 0 ? (
        people.length === 0 ? (
          <EmptyState
            symbol="◎"
            title="还没有可显示的人物"
            description="先在「添加资料」导入真实照片，再到「待确认的人物」确认身份。确认人物后，可在人物详情中建立关系。"
          />
        ) : (
          <EmptyState
            symbol="⌕"
            title="没有匹配的人"
            description="换个关键词，或把筛选切回「全部」。"
            action={
              <button
                type="button"
                className="button ghost"
                onClick={() => {
                  setQuery("");
                  setFilter("all");
                }}
              >
                清除筛选
              </button>
            }
          />
        )
      ) : (
        <>
          {frequent.length === 0 ? null : (
            <ul className="person-grid">
              {frequent.map((person) => (
                <PersonCard
                  key={person.id}
                  person={person}
                  selected={person.id === selectedId}
                  onSelect={onSelect}
                />
              ))}
            </ul>
          )}
          {rare.length === 0 ? null : (
            <details className="roster-rare">
              <summary>
                出现 2 次及以下的人物 · {rare.length} 人
                <small>展开查看</small>
              </summary>
              <ul className="person-grid">
                {rare.map((person) => (
                  <PersonCard
                    key={person.id}
                    person={person}
                    selected={person.id === selectedId}
                    onSelect={onSelect}
                  />
                ))}
              </ul>
            </details>
          )}
          <p className="roster-count">
            共 {people.length} 人
            {rare.length > 0 ? `，其中 ${rare.length} 人已折叠` : ""}
            {visible.length === people.length ? "" : `，当前显示 ${visible.length} 人`}
          </p>
        </>
      )}
    </section>
  );
}
