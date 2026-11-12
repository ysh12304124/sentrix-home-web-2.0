/**
 * 名录分层：**我的人物 / 可能认识 / 偶遇面孔**。纯函数，名录、选人列表、页头计数共用。
 *
 * 依据是实测形状（2026-11-12，album3 评测空间）：8 个已确认的人，47 个待确认的面孔里
 * 42 个只出现在 1～2 张照片。此前名录只按出现次数折叠，不看是否确认——出现 2 次的家人
 * 被折起来，出现 3 次的路人却留在主列表。这里先看用户的决定（确认、命名、标为路人），
 * 再看次数。
 */

import type { FamilyPerson } from "../../../shared/api/client";
import { isNamed, isPendingPerson, personDisplayName } from "./membership";

/** 待确认的面孔出现这么多次以上，才当作「可能认识」铺出来。 */
export const FREQUENT_FACE_MIN = 3;

export type PersonTier = "known" | "likely" | "passerby";

export const TIER_LABELS: Record<PersonTier, string> = {
  known: "我的人物",
  likely: "可能认识",
  passerby: "偶遇面孔",
};

/**
 * - 用户标成路人的 → 偶遇面孔（不论是否确认，这是用户的明确决定）；
 * - 已确认的 → 我的人物（出现几次都算，认出来的人不该被折叠）；
 * - 待确认但已有名字，或出现 ≥ 3 次 → 可能认识；
 * - 其余待确认的 → 偶遇面孔。
 */
export function personTier(person: FamilyPerson): PersonTier {
  if (person.hidden) return "passerby";
  if (!isPendingPerson(person)) return "known";
  if (isNamed(person) || person.appearance_count >= FREQUENT_FACE_MIN) return "likely";
  return "passerby";
}

export type Tiers = Record<PersonTier, FamilyPerson[]>;

/** 按层分开，层内保持传入顺序（调用方先排好序）。 */
export function splitByTier(people: readonly FamilyPerson[]): Tiers {
  const tiers: Tiers = { known: [], likely: [], passerby: [] };
  for (const person of people) tiers[personTier(person)].push(person);
  return tiers;
}

/** 排序：有名的在前 → 出现次数降序 → id 升序。**稳定**，不用依赖环境的 localeCompare。 */
export function sortPeople(people: readonly FamilyPerson[]): FamilyPerson[] {
  return [...people].sort((a, b) => {
    const named = Number(isNamed(b)) - Number(isNamed(a));
    if (named !== 0) return named;
    if (a.appearance_count !== b.appearance_count) return b.appearance_count - a.appearance_count;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

/**
 * 搜索命中：名字或任一别名包含关键词（不区分大小写）。返回命中的别名，名字命中或
 * 空关键词时为 `null`；不命中返回 `undefined`。
 */
export function matchPerson(person: FamilyPerson, needle: string): string | null | undefined {
  const query = needle.trim().toLowerCase();
  if (query === "") return null;
  if (personDisplayName(person).toLowerCase().includes(query)) return null;
  const alias = person.aliases.find((item) => item.toLowerCase().includes(query));
  return alias === undefined ? undefined : alias;
}
