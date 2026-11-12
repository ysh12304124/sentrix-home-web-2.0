/**
 * 名录：搜索 + 筛选 + 头像网格。**这是 `/people` 的默认视图**。
 *
 * 默认是名录不是图谱，依据是实测：刚导入的相册跑出来的图是 8 个孤点，把孤点图当
 * 默认视图是坏设计（计划书 §0）。名录至少先把「有这些人」摆清楚。
 *
 * 筛选里的 `unknown` 与 `family`/`friend` 平级，不是兜底分组——待归类的人必须和别的
 * 人一样出现在默认列表里（筛选 `all` 时），这正是 F7 要修的那条。
 *
 * 2026-11-12 起名录分三层（`strangers.ts`）：**我的人物**（已确认）→ **可能认识**（待确认
 * 但已有名字或反复出现）→ **偶遇面孔**（1～2 次的待确认面孔与标为路人的人，默认收起）。
 * 此前只按出现次数折叠，确认过的家人会被折起来，出现 3 次的路人却铺在最前面。
 */

import { useMemo, useState } from "react";

import type { FamilyPerson, FamilyMembershipValue } from "../../../shared/api/client";
import { EmptyState } from "../../../shared/ui/EmptyState";
import { MergePeopleDialog } from "./MergePeopleDialog";
import { PersonCard } from "./PersonCard";
import { membershipLabel, membershipOf } from "./membership";
import { StrangerTray } from "./StrangerTray";
import { matchPerson, sortPeople, splitByTier, TIER_LABELS } from "./strangers";

type Filter = "all" | FamilyMembershipValue;

const FILTERS: readonly { value: Filter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "family", label: membershipLabel("family") },
  { value: "friend", label: membershipLabel("friend") },
  { value: "unknown", label: membershipLabel("unknown") },
];

interface Props {
  readonly people: FamilyPerson[];
  readonly selectedId: string | null;
  readonly onSelect: (personId: string) => void;
  /** 手动合并成功后调用（参数是保留的人）。不给就不显示「合并人物」入口。 */
  readonly onMerged?: (targetId: string) => void | Promise<void>;
  /** 偶遇面孔的批量整理（标为路人 / 移除）完成后重拉。不给就不显示「批量整理」。 */
  readonly onChanged?: () => Promise<void>;
}

export function FamilyRoster({ people, selectedId, onSelect, onMerged, onChanged }: Props) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  // 合并模式：卡片变成勾选；勾满两个以上、且同一相册，才能打开确认框。
  const [merging, setMerging] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [confirming, setConfirming] = useState(false);
  // 偶遇面孔区「合并为同一人」交过来的人；与合并模式的勾选互不干扰。
  const [trayMerge, setTrayMerge] = useState<FamilyPerson[] | null>(null);

  const pickedPeople = picked
    .map((id) => people.find((person) => person.id === id))
    .filter((person): person is FamilyPerson => person !== undefined);
  const mixedScopes = new Set(pickedPeople.map((person) => person.scope_id)).size > 1;

  function exitMerge() {
    setMerging(false);
    setPicked([]);
    setConfirming(false);
  }

  function togglePick(personId: string) {
    setPicked((current) =>
      current.includes(personId) ? current.filter((id) => id !== personId) : [...current, personId],
    );
  }

  const cardSelect = merging ? togglePick : onSelect;
  const isSelected = (personId: string) => (merging ? picked.includes(personId) : personId === selectedId);

  const counts = useMemo(() => {
    const base: Record<Filter, number> = { all: people.length, family: 0, friend: 0, unknown: 0 };
    for (const person of people) base[membershipOf(person)] += 1;
    return base;
  }, [people]);

  const visible = useMemo(
    () =>
      sortPeople(people).filter((person) => {
        if (filter !== "all" && membershipOf(person) !== filter) return false;
        return matchPerson(person, query) !== undefined;
      }),
    [people, query, filter],
  );
  const tiers = useMemo(() => splitByTier(visible), [visible]);
  const searching = query.trim() !== "" || filter !== "all";

  const grid = (items: readonly FamilyPerson[]) => (
    <ul className="person-grid">
      {items.map((person) => (
        <PersonCard
          key={person.id}
          person={person}
          selected={isSelected(person.id)}
          onSelect={cardSelect}
          picking={merging}
        />
      ))}
    </ul>
  );

  return (
    <section className="family-roster">
      <div className="roster-toolbar">
        <input
          type="search"
          className="roster-search"
          aria-label="搜索人物"
          placeholder="搜名字或别名…"
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
        {onMerged === undefined || people.length < 2 ? null : (
          <button
            type="button"
            className={merging ? "button ghost small active" : "button ghost small"}
            aria-pressed={merging}
            onClick={() => (merging ? exitMerge() : setMerging(true))}
          >
            {merging ? "退出合并" : "合并人物"}
          </button>
        )}
      </div>

      {merging ? (
        <div className="roster-merge-bar" role="region" aria-label="合并人物">
          <span>
            {picked.length === 0
              ? "点选同一个人的几张卡片（年龄、光线不同被分开的也算），再点「合并」。"
              : `已选 ${picked.length} 人`}
            {mixedScopes ? " · 只能合并同一相册里的人物" : ""}
          </span>
          <button
            type="button"
            className="button primary small"
            disabled={picked.length < 2 || mixedScopes}
            onClick={() => setConfirming(true)}
          >
            合并所选
          </button>
          <button type="button" className="button ghost small" onClick={exitMerge}>
            取消
          </button>
        </div>
      ) : null}

      {confirming && onMerged !== undefined ? (
        <MergePeopleDialog
          people={pickedPeople}
          onCancel={() => setConfirming(false)}
          onMerged={async (targetId) => {
            exitMerge();
            await onMerged(targetId);
          }}
        />
      ) : null}

      {trayMerge !== null && onMerged !== undefined ? (
        <MergePeopleDialog
          people={trayMerge}
          onCancel={() => setTrayMerge(null)}
          onMerged={async (targetId) => {
            setTrayMerge(null);
            await onMerged(targetId);
          }}
        />
      ) : null}

      {visible.length === 0 ? (
        people.length === 0 ? (
          <EmptyState
            symbol="◎"
            title="还没有可显示的人物"
            description="先在「添加资料」导入真实照片。系统整理出的人物会出现在这里，点开详情即可确认身份、建立关系。"
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
          <section className="roster-tier" aria-label={TIER_LABELS.known}>
            <h3 className="roster-tier-title">
              {TIER_LABELS.known} <span>{tiers.known.length}</span>
            </h3>
            {tiers.known.length === 0 ? (
              <p className="detail-empty">
                {searching ? "已确认的人里没有匹配的。" : "还没有确认过的人物。先从下面「可能认识」里认出几个人。"}
              </p>
            ) : (
              grid(tiers.known)
            )}
          </section>
          {tiers.likely.length === 0 ? null : (
            <section className="roster-tier likely" aria-label={TIER_LABELS.likely}>
              <h3 className="roster-tier-title">
                {TIER_LABELS.likely} <span>{tiers.likely.length}</span>
              </h3>
              <p className="roster-tier-hint">这些面孔在照片里反复出现或已有名字，点开即可确认身份。</p>
              {grid(tiers.likely)}
            </section>
          )}
          <StrangerTray
            people={tiers.passerby}
            forceOpen={searching}
            selectedId={selectedId}
            onSelect={onSelect}
            merging={merging}
            isPicked={(id) => picked.includes(id)}
            onPick={togglePick}
            onMerge={(items) => setTrayMerge(items)}
            onChanged={onChanged}
          />
          <p className="roster-count">
            共 {people.length} 人
            {searching ? `，当前显示 ${visible.length} 人` : ""}
          </p>
        </>
      )}
    </section>
  );
}
