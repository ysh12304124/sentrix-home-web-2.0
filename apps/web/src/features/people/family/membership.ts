/**
 * `membership` 与亲属谓词的**唯一词表**：界面上的中文、筛选值、建关系时的下拉项
 * 全部从这里来，别处不再各写一份字符串。
 *
 * 关系优先级 / 长辈谓词那三张表**逐字搬自 1.0 `src/people_graph.js:6-23`**
 * （计划书 §3.5），不重新发明——1.0 的测试钉的就是它们。
 */

import type { FamilyMembershipValue, FamilyPerson } from "../../../shared/api/client";
import { publicPersonName, UNNAMED_PERSON_NAME } from "../reviewErrors";

/** 筛选与下拉的顺序即此。`unknown` 排在最后：它是默认态，不是异常态。 */
export const MEMBERSHIP_VALUES: readonly FamilyMembershipValue[] = ["family", "friend", "unknown"];

const MEMBERSHIP_LABELS: Record<FamilyMembershipValue, string> = {
  family: "家人",
  friend: "朋友",
  unknown: "待归类",
};

export function membershipLabel(value: FamilyMembershipValue): string {
  return MEMBERSHIP_LABELS[value];
}

/**
 * 一个人的归属。
 *
 * **`membership === null` 与 `"unknown"` 归成同一个值**：两者都是「没被推断归类过」。
 * 1.0 把这两类都藏进另一个 tab 的纯文字列表，实测数据（8 个人全是 unknown）下图谱
 * 因此是一张空图——F7 的核心改动就是让它们留在名录与图谱里，所以这里先统一成一个
 * 前端可判定的值，界面不再区分「没这一行」和「这一行写着 unknown」。
 */
export function membershipOf(person: FamilyPerson): FamilyMembershipValue {
  const raw = person.membership?.membership;
  return raw === "family" || raw === "friend" ? raw : "unknown";
}

/** 后端给未命名人物的占位串。与簇/人物介绍用同一句用户文案。 */
export const UNNAMED = UNNAMED_PERSON_NAME;

/** 显示名：空串与内部 cluster id 都收成用户文案，不把内部 id 带进界面。 */
export function personDisplayName(person: FamilyPerson): string {
  return publicPersonName(person.display_name);
}

/** 有没有真名字。占位串不算——「有名在前」的排序要按这一条。 */
export function isNamed(person: FamilyPerson): boolean {
  const name = personDisplayName(person);
  return name !== UNNAMED_PERSON_NAME;
}

/**
 * 谓词优先级。数值越小越优先，一对人只画一条边时用它决胜。
 * 数字是 1.0 原样：直系亲属（父/母）比配偶更靠前，朋友最后。
 */
export const relationPriority = new Map<string, number>([
  ["父亲", 1], ["母亲", 1], ["丈夫", 2], ["妻子", 2],
  ["祖父", 3], ["祖母", 3], ["外祖父", 3], ["外祖母", 3],
  ["哥哥", 4], ["姐姐", 4], ["弟弟", 4], ["妹妹", 4],
  ["叔叔", 5], ["伯伯", 5], ["姑姑", 5], ["舅舅", 5], ["姨妈", 5], ["姨母", 5],
  ["朋友", 8], ["密友", 8], ["其他亲属", 9],
]);

/**
 * 参与辈分传播的谓词。**1.0 只有这六个，叔伯姑舅姨不在内**——照搬，不顺手补全：
 * 补了会改变分层结果，而这一段的裁决是「方向原样保留、语义不在这一段里改」。
 */
export const parentRelations = new Set(["父亲", "母亲", "祖父", "祖母", "外祖父", "外祖母"]);

/** 配偶。1.0 声明了但布局没用它；留着是因为词表是唯一来源，别处要问「是不是配偶」时不该自己再列一遍。 */
export const partnerRelations = new Set(["丈夫", "妻子"]);

/** 朋友。同上：词表留全，布局只用 membership 判行。 */
export const friendRelations = new Set(["朋友", "密友"]);

export interface RelationChoice {
  readonly label: string;
  readonly predicate: string;
  readonly inversePredicate: string;
}

/** 建关系下拉里的十一条，1.0 `people_graph.js:17-23` 逐字。 */
export function relationChoices(): RelationChoice[] {
  return [
    ["父亲", "儿子"], ["父亲", "女儿"], ["母亲", "儿子"], ["母亲", "女儿"],
    ["丈夫", "妻子"], ["妻子", "丈夫"], ["哥哥", "弟弟"], ["姐姐", "妹妹"],
    ["朋友", "朋友"], ["密友", "密友"], ["其他亲属", "其他亲属"],
  ].map(([predicate, inversePredicate]) => ({
    label: `${predicate} → ${inversePredicate}`,
    predicate,
    inversePredicate,
  }));
}
