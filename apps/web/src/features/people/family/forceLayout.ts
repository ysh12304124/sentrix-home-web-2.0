/**
 * 关系图布局（2026-11-12 重构）。**纯函数，不碰 DOM**，单测可以直接钉坐标性质。
 *
 * 参考成熟知识图谱的两类布局：
 *
 * 1. **总览：分层力导向**（`forceLayout`）。力导向是 Neo4j Browser、Obsidian、Gephi
 *    （ForceAtlas2）、Cytoscape（fCoSE）这类关系图的通用做法；家谱又要求长辈在上，所以
 *    在 d3-force 的「连线弹簧 + 节点斥力 + 碰撞」之外，给参与亲子关系的人加一股拉向
 *    「辈分行」的纵向力——相当于把 Sugiyama 分层当软约束。朋友、同事等自定义关系没有
 *    辈分，只受很弱的居中力，自然分布。
 * 2. **聚焦：以某人为中心的环形布局**（`radialLayout`）。Neo4j Bloom 一类工具的 ego 视图：
 *    选中的人在圆心，直接关系在第一圈、隔一层的在第二圈，其余放最外圈。
 *
 * **可复现**：初始坐标由辈分与排序决定，d3-force v3 的随机源是固定种子的 LCG，迭代步数
 * 固定且同步跑完——同样的数据每次排出同样的图。
 */

import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";

import type { FamilyPerson } from "../../../shared/api/client";
import type { LayoutPoint, RelationEdge } from "./graphLayout";
import { NODE_RADIUS } from "./graphLayout";
import { canonicalKinship, parentRelations, partnerRelations } from "./membership";

export type EdgeKind = "parent" | "partner" | "other";

/** 一条边的长幼方向：谁是长辈。两行里任一行的称谓是长辈称谓都算（去重可能留下的是反行）。 */
export function elderOf(edge: RelationEdge): { elder: string; younger: string } | null {
  if (parentRelations.has(canonicalKinship(edge.predicate))) {
    return { elder: edge.subject_entity_id, younger: edge.object_entity_id };
  }
  if (parentRelations.has(canonicalKinship(edge.inverse_predicate))) {
    return { elder: edge.object_entity_id, younger: edge.subject_entity_id };
  }
  return null;
}

export function edgeKind(edge: RelationEdge): EdgeKind {
  if (elderOf(edge) !== null) return "parent";
  if (partnerRelations.has(canonicalKinship(edge.predicate))) return "partner";
  if (partnerRelations.has(canonicalKinship(edge.inverse_predicate))) return "partner";
  return "other";
}

/** 布局参数。导出给测试与视图共用。 */
export const FORCE = {
  /** 相邻辈分行的间距。 */
  generationGap: 170,
  /** 不同关系的理想连线长度：夫妻最近、亲子其次、其他关系最远。 */
  linkDistance: { partner: 120, parent: 160, other: 210 } as Record<EdgeKind, number>,
  /** 碰撞半径要给节点下方的名字留位置。 */
  collideRadius: NODE_RADIUS + 30,
  charge: -620,
  /** 有辈分的人被拉向辈分行的强度；没有辈分的人只受很弱的居中力。 */
  generationStrength: 0.45,
  centerStrength: 0.03,
  ticks: 320,
  /** 环形布局每一圈的半径增量。 */
  ringGap: 190,
} as const;

/**
 * 辈分：`level[younger] = max(level[younger], level[elder] + 1)`，松弛到不动点。
 * 只有出现在亲子边上的人才有辈分（返回值里才有他）。迭代上限取人数，关系里有环时截断。
 */
export function generations(people: readonly FamilyPerson[], edges: readonly RelationEdge[]): Map<string, number> {
  const ids = new Set(people.map((person) => person.id));
  const pairs = edges
    .map(elderOf)
    .filter((pair): pair is { elder: string; younger: string } => pair !== null)
    .filter((pair) => ids.has(pair.elder) && ids.has(pair.younger));
  const levels = new Map<string, number>();
  for (const pair of pairs) {
    levels.set(pair.elder, levels.get(pair.elder) ?? 0);
    levels.set(pair.younger, levels.get(pair.younger) ?? 0);
  }
  const partners = edges
    .filter((edge) => edgeKind(edge) === "partner")
    .filter((edge) => ids.has(edge.subject_entity_id) && ids.has(edge.object_entity_id));
  // 两条规则一起松弛到不动点：晚辈至少比长辈低一层；夫妻同层（取两人里更低的那层，
  // 例如妈妈只和孩子连着时起初在第 0 层，和爸爸对齐后与他同在第 1 层）。
  // 迭代上限取人数的两倍，关系里有环时截断，不能让布局挂住。
  for (let round = 0; round < people.length * 2 + 2; round += 1) {
    let changed = false;
    for (const pair of pairs) {
      const next = (levels.get(pair.elder) ?? 0) + 1;
      if (next > (levels.get(pair.younger) ?? 0)) {
        levels.set(pair.younger, next);
        changed = true;
      }
    }
    for (const edge of partners) {
      const a = levels.get(edge.subject_entity_id);
      const b = levels.get(edge.object_entity_id);
      if (a === undefined && b === undefined) continue;
      const level = Math.max(a ?? 0, b ?? 0);
      if (a !== level || b !== level) {
        levels.set(edge.subject_entity_id, level);
        levels.set(edge.object_entity_id, level);
        changed = true;
      }
    }
    if (!changed) break;
  }
  return levels;
}

interface Node extends SimulationNodeDatum {
  id: string;
  level: number | null;
}

interface Link extends SimulationLinkDatum<Node> {
  kind: EdgeKind;
}

/** 稳定排序：出现多的在前，再按 id。决定初始横向位置，进而让结果可复现。 */
function stableOrder(people: readonly FamilyPerson[]): FamilyPerson[] {
  return [...people].sort((a, b) =>
    a.appearance_count !== b.appearance_count ? b.appearance_count - a.appearance_count : a.id < b.id ? -1 : 1,
  );
}

export interface ForceLayoutOptions {
  /** 用户拖过的人：固定在这个位置，其他人围着他重新排。 */
  readonly pins?: ReadonlyMap<string, LayoutPoint>;
  /** 上一次的坐标：重排时从这里出发，避免整张图跳动。 */
  readonly previous?: ReadonlyMap<string, LayoutPoint>;
}

/** 总览：分层力导向。坐标以 (0, 0) 为大致中心，视图负责自适应缩放。 */
export function forceLayout(
  people: readonly FamilyPerson[],
  edges: readonly RelationEdge[],
  options: ForceLayoutOptions = {},
): Map<string, LayoutPoint> {
  const result = new Map<string, LayoutPoint>();
  if (people.length === 0) return result;
  const levels = generations(people, edges);
  const maxLevel = Math.max(0, ...levels.values());
  const levelY = (level: number) => (level - maxLevel / 2) * FORCE.generationGap;

  const ordered = stableOrder(people);
  const nodes: Node[] = ordered.map((person, index) => {
    const level = levels.get(person.id) ?? null;
    const previous = options.previous?.get(person.id);
    const pin = options.pins?.get(person.id);
    // 初始位置：有辈分的放在自己那一行，没有的绕圆周摊开（不共点，斥力才算得出方向）。
    const angle = (index / ordered.length) * Math.PI * 2;
    const start = previous ?? {
      x: level === null ? Math.cos(angle) * 260 : (index - ordered.length / 2) * 60,
      y: level === null ? Math.sin(angle) * 260 : levelY(level),
    };
    return { id: person.id, level, x: start.x, y: start.y, fx: pin?.x, fy: pin?.y };
  });
  const ids = new Set(nodes.map((node) => node.id));
  const links: Link[] = edges
    .filter((edge) => ids.has(edge.subject_entity_id) && ids.has(edge.object_entity_id))
    .map((edge) => ({ source: edge.subject_entity_id, target: edge.object_entity_id, kind: edgeKind(edge) }));

  const simulation = forceSimulation<Node>(nodes)
    .stop()
    .force(
      "link",
      forceLink<Node, Link>(links)
        .id((node) => node.id)
        .distance((link) => FORCE.linkDistance[link.kind])
        .strength(0.7),
    )
    .force("charge", forceManyBody<Node>().strength(FORCE.charge).distanceMax(900))
    .force("collide", forceCollide<Node>(FORCE.collideRadius))
    .force("x", forceX<Node>(0).strength(FORCE.centerStrength))
    .force(
      "y",
      forceY<Node>((node) => (node.level === null ? 0 : levelY(node.level))).strength((node) =>
        node.level === null ? FORCE.centerStrength : FORCE.generationStrength,
      ),
    );
  // 从上一次的坐标出发时只需小幅调整，降低初始「温度」，图不会整体漂移。
  if (options.previous !== undefined) simulation.alpha(0.3);
  for (let tick = 0; tick < FORCE.ticks; tick += 1) simulation.tick();

  for (const node of nodes) result.set(node.id, { x: node.x ?? 0, y: node.y ?? 0 });
  return result;
}

/** 从 `focusId` 出发的关系距离（无向 BFS）。到不了的人不在结果里。 */
export function relationDistances(focusId: string, edges: readonly RelationEdge[]): Map<string, number> {
  const neighbours = new Map<string, string[]>();
  for (const edge of edges) {
    neighbours.set(edge.subject_entity_id, [...(neighbours.get(edge.subject_entity_id) ?? []), edge.object_entity_id]);
    neighbours.set(edge.object_entity_id, [...(neighbours.get(edge.object_entity_id) ?? []), edge.subject_entity_id]);
  }
  const distances = new Map<string, number>([[focusId, 0]]);
  const queue = [focusId];
  while (queue.length > 0) {
    const current = queue.shift() as string;
    for (const next of neighbours.get(current) ?? []) {
      if (distances.has(next)) continue;
      distances.set(next, (distances.get(current) ?? 0) + 1);
      queue.push(next);
    }
  }
  return distances;
}

/**
 * 聚焦：`focusId` 在圆心，第 n 跳的人在第 n 圈；到不了的人放在最外圈。
 * 第二圈以后的人尽量靠近把他带进来的那位的角度，减少连线交叉。
 */
export function radialLayout(
  focusId: string,
  people: readonly FamilyPerson[],
  edges: readonly RelationEdge[],
): Map<string, LayoutPoint> {
  const result = new Map<string, LayoutPoint>();
  if (!people.some((person) => person.id === focusId)) return result;
  const distances = relationDistances(focusId, edges);
  const maxDepth = Math.max(0, ...[...distances.values()]);
  const outer = maxDepth + 1;
  const depthOf = (id: string) => distances.get(id) ?? outer;

  const rings = new Map<number, FamilyPerson[]>();
  for (const person of stableOrder(people)) {
    const depth = depthOf(person.id);
    rings.set(depth, [...(rings.get(depth) ?? []), person]);
  }

  const angles = new Map<string, number>([[focusId, 0]]);
  result.set(focusId, { x: 0, y: 0 });
  for (const depth of [...rings.keys()].sort((a, b) => a - b)) {
    if (depth === 0) continue;
    const ring = rings.get(depth) ?? [];
    // 按「上一圈里相邻的人的角度」排序，让同一支的人挨在一起。
    const parentAngle = (id: string) => {
      const angle = edges
        .filter((edge) => edge.subject_entity_id === id || edge.object_entity_id === id)
        .map((edge) => (edge.subject_entity_id === id ? edge.object_entity_id : edge.subject_entity_id))
        .filter((other) => depthOf(other) === depth - 1)
        .map((other) => angles.get(other))
        .find((value) => value !== undefined);
      return angle ?? Number.POSITIVE_INFINITY;
    };
    const sorted = [...ring].sort((a, b) => parentAngle(a.id) - parentAngle(b.id));
    sorted.forEach((person, index) => {
      // 从正上方开始顺时针摊开。
      const angle = -Math.PI / 2 + (index / sorted.length) * Math.PI * 2;
      angles.set(person.id, angle);
      const radius = depth * FORCE.ringGap;
      result.set(person.id, { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius });
    });
  }
  return result;
}
