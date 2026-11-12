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

/** 确认身份时可选的家庭角色（1.0 ``app.js:956`` 的 ``<select>``，原样保留）。 */
export const FAMILY_ROLES: readonly string[] = ["母亲", "父亲", "孩子", "祖父母", "其他家庭成员"];

/** 实体是不是还待确认。只认后端给的 `status`，前端不从名字或出现次数反推。 */
export function isPendingPerson(person: FamilyPerson): boolean {
  return person.status === "pending";
}

/**
 * 默认保留谁：有名字的 > 已确认的 > 出现多的 > id 小的。
 * 与名录排序同一个思路——用户最可能想保留的是那个「已经认识的人」。
 */
export function defaultMergeTarget(people: readonly FamilyPerson[]): string {
  const ranked = [...people].sort((a, b) => {
    const named = Number(isNamed(b)) - Number(isNamed(a));
    if (named !== 0) return named;
    const confirmed = Number(!isPendingPerson(b)) - Number(!isPendingPerson(a));
    if (confirmed !== 0) return confirmed;
    if (a.appearance_count !== b.appearance_count) return b.appearance_count - a.appearance_count;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
  return ranked[0]?.id ?? "";
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

/**
 * 合法的正反称谓，**与后端 `domain/identity/family_graph.py` 的 `RELATION_INVERSES` 同表**：
 * 键是「对方是我的什么人」，值是「那我是对方的什么人」的可选项。后端会再校验一次，
 * 这里多一份是为了在提交前就只给出合法组合，并由 `membership.test.ts` 钉住两边一致。
 */
export const RELATION_INVERSES: Readonly<Record<string, readonly string[]>> = {
  丈夫: ["妻子"],
  妻子: ["丈夫"],
  父亲: ["儿子", "女儿"],
  母亲: ["儿子", "女儿"],
  儿子: ["父亲", "母亲"],
  女儿: ["父亲", "母亲"],
  哥哥: ["哥哥", "姐姐", "弟弟", "妹妹"],
  姐姐: ["哥哥", "姐姐", "弟弟", "妹妹"],
  弟弟: ["哥哥", "姐姐", "弟弟", "妹妹"],
  妹妹: ["哥哥", "姐姐", "弟弟", "妹妹"],
  祖父: ["孙子", "孙女"],
  祖母: ["孙子", "孙女"],
  外祖父: ["外孙", "外孙女"],
  外祖母: ["外孙", "外孙女"],
  孙子: ["祖父", "祖母"],
  孙女: ["祖父", "祖母"],
  外孙: ["外祖父", "外祖母"],
  外孙女: ["外祖父", "外祖母"],
  叔叔: ["侄子", "侄女"],
  伯伯: ["侄子", "侄女"],
  姑姑: ["侄子", "侄女"],
  舅舅: ["外甥", "外甥女"],
  姨妈: ["外甥", "外甥女"],
  姨母: ["外甥", "外甥女"],
  侄子: ["叔叔", "伯伯", "姑姑"],
  侄女: ["叔叔", "伯伯", "姑姑"],
  外甥: ["舅舅", "姨妈", "姨母"],
  外甥女: ["舅舅", "姨妈", "姨母"],
  朋友: ["朋友"],
  密友: ["密友"],
  其他亲属: ["其他亲属"],
};

export interface RoleGroup {
  readonly label: string;
  readonly roles: readonly string[];
}

/**
 * 建关系时「对方是我的 ___」的下拉分组。`姨母` 与 `姨妈` 同义，下拉只给 `姨妈`；
 * 库里已有的 `姨母` 照常显示。
 */
export const RELATION_ROLE_GROUPS: readonly RoleGroup[] = [
  { label: "长辈", roles: ["父亲", "母亲", "祖父", "祖母", "外祖父", "外祖母", "叔叔", "伯伯", "姑姑", "舅舅", "姨妈"] },
  { label: "同辈", roles: ["丈夫", "妻子", "哥哥", "姐姐", "弟弟", "妹妹"] },
  { label: "晚辈", roles: ["儿子", "女儿", "孙子", "孙女", "外孙", "外孙女", "侄子", "侄女", "外甥", "外甥女"] },
  { label: "其他", roles: ["朋友", "密友", "其他亲属"] },
];

/**
 * 对方是我的 `role` 时，我可以是对方的什么人。
 *
 * 后端对兄弟姐妹四个称谓互相放行（`哥哥 ↔ 哥哥` 也合法），但「对方是我哥哥」时我只可能
 * 是弟弟或妹妹，所以这里按长幼收窄；其余与 `RELATION_INVERSES` 一致。只有一项时界面
 * 直接用它，不再多问。
 */
export function myRoleOptions(role: string): readonly string[] {
  if (role === "哥哥" || role === "姐姐") return ["弟弟", "妹妹"];
  if (role === "弟弟" || role === "妹妹") return ["哥哥", "姐姐"];
  return RELATION_INVERSES[role] ?? [];
}

/**
 * 站在 `selfId` 这个人的角度，读一条（已归并的）关系：对方是谁、对方是我的什么人。
 *
 * 一条边 `subject —predicate→ object` 的意思是「subject 是 object 的 predicate」，
 * `inverse_predicate` 是「object 是 subject 的什么人」。所以我是 subject 时，对方的
 * 称谓是 `inverse_predicate`；我是 object 时，对方的称谓是 `predicate`。
 * 此前详情把这两者对调了，B 的档案里 A 显示成「儿子」，看起来像关系只写了一边。
 */
export function relationFromSelf(
  edge: { subject_entity_id: string; object_entity_id: string; predicate: string; inverse_predicate: string },
  selfId: string,
): { otherId: string; otherRole: string; selfRole: string } {
  const selfIsSubject = edge.subject_entity_id === selfId;
  return selfIsSubject
    ? { otherId: edge.object_entity_id, otherRole: edge.inverse_predicate, selfRole: edge.predicate }
    : { otherId: edge.subject_entity_id, otherRole: edge.predicate, selfRole: edge.inverse_predicate };
}
