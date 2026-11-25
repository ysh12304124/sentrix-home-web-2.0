/**
 * 名录：**我的人物 / 待认识** 两个视图（2026-11-12 重排）。
 *
 * 此前「我的人物」「可能认识」「偶遇面孔」三段竖向并列，但它们不是同一类东西：
 * 「我的人物」是已经认出来的人——这一页的主体；后两段是还没认出来的面孔——一份待办。
 * 所以拆成两个视图，用顶部的分段切换：
 *
 * - **我的人物**：已确认的人（标为路人的除外），带「家人 / 朋友 / 待归类」筛选——这组
 *   筛选只对认出来的人有意义，原来它连没认出的面孔一起数（「待归类 53」）；
 * - **待认识**：常出现的面孔（`likely`）与偶遇面孔（`passerby`，可批量整理）。
 *
 * 搜索在当前视图里找；另一个视图也有匹配时给一句提示，一点就切过去。
 * 没有「合并人物」入口：合并改为改名撞名时询问（见 `SameNameDialog`）。
 */

import { useMemo, useState } from "react";

import type { FamilyPerson, FamilyMembershipValue } from "../../../shared/api/client";
import { EmptyState } from "../../../shared/ui/EmptyState";
import { PersonCard } from "./PersonCard";
import { membershipLabel, membershipOf } from "./membership";
import { StrangerTray } from "./StrangerTray";
import { matchPerson, sortPeople, splitByTier } from "./strangers";

type View = "known" | "inbox";
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
  /** 偶遇面孔的批量整理（标为路人 / 移除）完成后重拉。不给就不显示「批量整理」。 */
  readonly onChanged?: () => Promise<void>;
}

export function FamilyRoster({ people, selectedId, onSelect, onChanged }: Props) {
  const tiers = useMemo(() => splitByTier(sortPeople(people)), [people]);
  const inboxCount = tiers.likely.length + tiers.passerby.length;
  // 还一个人都没认出来时，默认停在「待认识」——那里才有事可做。
  const [view, setView] = useState<View>(() => (tiers.known.length === 0 && inboxCount > 0 ? "inbox" : "known"));
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  const matches = (person: FamilyPerson) => matchPerson(person, query) !== undefined;
  const known = tiers.known.filter((person) => (filter === "all" || membershipOf(person) === filter) && matches(person));
  const likely = tiers.likely.filter(matches);
  const passerby = tiers.passerby.filter(matches);
  const searching = query.trim() !== "";
  const elsewhere = !searching
    ? 0
    : view === "known"
      ? likely.length + passerby.length
      : tiers.known.filter(matches).length;

  const counts = useMemo(() => {
    const base: Record<Filter, number> = { all: tiers.known.length, family: 0, friend: 0, unknown: 0 };
    for (const person of tiers.known) base[membershipOf(person)] += 1;
    return base;
  }, [tiers.known]);

  const grid = (items: readonly FamilyPerson[]) => (
    <ul className="person-grid">
      {items.map((person) => (
        <PersonCard key={person.id} person={person} selected={person.id === selectedId} onSelect={onSelect} />
      ))}
    </ul>
  );

  if (people.length === 0) {
    return (
      <EmptyState
        symbol="◎"
        title="还没有可显示的人物"
        description="先在「添加资料」导入真实照片。系统整理出的人物会出现在这里，点开详情即可确认身份、建立关系。"
      />
    );
  }

  return (
    <section className="family-roster">
      <div className="roster-toolbar">
        <div className="roster-views" role="tablist" aria-label="名录视图">
          {(
            [
              ["known", "我的人物", tiers.known.length],
              ["inbox", "待认识", inboxCount],
            ] as const
          ).map(([value, label, count]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={view === value}
              className={view === value ? "roster-view active" : "roster-view"}
              onClick={() => setView(value)}
            >
              {label}
              <span className="roster-filter-count">{count}</span>
            </button>
          ))}
        </div>
        <input
          type="search"
          className="roster-search"
          aria-label="搜索人物"
          placeholder="搜名字或别名…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      {elsewhere === 0 ? null : (
        <p className="roster-elsewhere">
          「{view === "known" ? "待认识" : "我的人物"}」里还有 {elsewhere} 个匹配。
          <button type="button" className="text-button" onClick={() => setView(view === "known" ? "inbox" : "known")}>
            去看看
          </button>
        </p>
      )}

      {view === "known" ? (
        <section className="roster-tier" aria-label="我的人物">
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
          {known.length > 0 ? (
            grid(known)
          ) : tiers.known.length === 0 ? (
            <EmptyState
              symbol="◎"
              title="还没有认出任何人"
              description={`「待认识」里有 ${inboxCount} 张面孔，点开给他们起个名字。`}
              action={
                <button type="button" className="button ghost" onClick={() => setView("inbox")}>
                  去认识
                </button>
              }
            />
          ) : (
            <p className="detail-empty">没有匹配的人。换个关键词，或把筛选切回「全部」。</p>
          )}
        </section>
      ) : (
        <>
          <section className="roster-tier" aria-label="常出现的面孔">
            <h3 className="roster-tier-title">
              常出现的面孔 <span>{likely.length}</span>
            </h3>
            <p className="roster-tier-hint">在照片里反复出现或已有名字，点开给 TA 确认身份。</p>
            {likely.length === 0 ? <p className="detail-empty">没有了。</p> : grid(likely)}
          </section>
          <StrangerTray
            people={passerby}
            defaultOpen
            forceOpen={searching}
            selectedId={selectedId}
            onSelect={onSelect}
            onChanged={onChanged}
          />
        </>
      )}
    </section>
  );
}
