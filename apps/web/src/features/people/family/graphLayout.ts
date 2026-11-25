/**
 * 关系图的公共几何：去重、视图变换（自适应 / 缩放 / 屏幕↔世界）、拖拽落点判定。
 * **纯函数，不碰 DOM**。节点坐标本身由 `forceLayout.ts`（分层力导向 / 环形聚焦）给出；
 * 原先的固定间距分层布局 `graphLayout()` 已于 2026-11-12 被它取代。
 */

import type { FamilyRelationship } from "../../../shared/api/client";
import { canonicalKinship, relationPriority } from "./membership";

/** 一对人之间那条边。方向保留（subject 是长辈、object 是晚辈）。 */
export interface RelationEdge {
  readonly subject_entity_id: string;
  readonly object_entity_id: string;
  readonly predicate: string;
  readonly inverse_predicate: string;
}

export interface LayoutPoint {
  readonly x: number;
  readonly y: number;
}


/** 无向 pair 的键。一对人无论从哪个方向来都落到同一个键上。 */
function pairKey(a: string, b: string): string {
  return a < b ? `${a}\u0000${b}` : `${b}\u0000${a}`;
}

/** 优先级小者胜；同优先级按谓词字典序。返回负数表示 a 更优。 */
function compareEdges(a: FamilyRelationship, b: FamilyRelationship): number {
  const pa = relationPriority.get(canonicalKinship(a.predicate)) ?? Number.MAX_SAFE_INTEGER;
  const pb = relationPriority.get(canonicalKinship(b.predicate)) ?? Number.MAX_SAFE_INTEGER;
  if (pa !== pb) return pa - pb;
  return a.predicate < b.predicate ? -1 : a.predicate > b.predicate ? 1 : 0;
}

/**
 * 一对人只留一条边。
 *
 * 后端的库里一条关系是**两行**（正行 + 反行），同一对人还可能同时被推断出多种关系。
 * 1.0 按无向 pair 归并、`relationPriority` 小者胜（`people_graph.js:43-52`），这里
 * 照搬。**代价是「既是亲戚又是同事」时只显示一条**——已知的信息丢失，F7 原样保留，
 * 不在这一段里改语义。
 */
export function dedupeRelationships(
  relationships: readonly FamilyRelationship[],
): RelationEdge[] {
  const best = new Map<string, FamilyRelationship>();
  for (const relation of relationships) {
    if (relation.subject_entity_id === relation.object_entity_id) continue;
    const key = pairKey(relation.subject_entity_id, relation.object_entity_id);
    const current = best.get(key);
    if (current === undefined || compareEdges(relation, current) < 0) {
      best.set(key, relation);
    }
  }
  return [...best.values()].map((relation) => ({
    subject_entity_id: relation.subject_entity_id,
    object_entity_id: relation.object_entity_id,
    predicate: relation.predicate,
    inverse_predicate: relation.inverse_predicate,
  }));
}

/**
 * 用户确认过的关系。模型推断与角色补边（`model` / `role_seed`）都不算——
 * 关系图谱只画用户真正认过的边，没有已确认关系时显示空状态。
 */
export function isConfirmedRelationship(relation: FamilyRelationship): boolean {
  return relation.locked || relation.source === "user_override";
}

/** 过滤 + 归并。图谱与详情侧栏都用这一条，避免两处判据漂开。 */
export function confirmedRelationships(
  relationships: readonly FamilyRelationship[],
): RelationEdge[] {
  return dedupeRelationships(relationships.filter(isConfirmedRelationship));
}

/** 所有节点的包围盒。视图用它算初始 scale/translate；空结果返回 null。 */
export function layoutBounds(
  layout: ReadonlyMap<string, LayoutPoint>,
): { minX: number; minY: number; maxX: number; maxY: number } | null {
  if (layout.size === 0) return null;
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const point of layout.values()) {
    if (point.x < minX) minX = point.x;
    if (point.y < minY) minY = point.y;
    if (point.x > maxX) maxX = point.x;
    if (point.y > maxY) maxY = point.y;
  }
  return { minX, minY, maxX, maxY };
}

// ---- 视图变换（纯函数） -----------------------------------------------------------
//
// 这段数学原先埋在 `FamilyGraphView` 几个事件处理里，一行测试都没有：`fit` 的居中项
// 漏写、滚轮的缩放不动点写反、屏幕↔世界的乘除颠倒，三种都只会让画面错位而不会报错。
// 抽成纯函数是为了能定点变异地测它们——**公式与常量原样搬出，未改行为**。

export interface Transform {
  readonly k: number;
  readonly x: number;
  readonly y: number;
}

/** 节点渲染尺寸。视图画圈与 `fitTransform` 算包围盒共用同一份，不再两处各写一个数。 */
export const NODE_RADIUS = 26;
/** 名字画在节点下方，包围盒下沿要多留的高度。 */
export const NODE_LABEL_HEIGHT = 34;
/** 自适应时四周的留白。 */
export const FIT_MARGIN = 20;
export const FIT_MAX_SCALE = 1.4;
export const FIT_MIN_SCALE = 0.15;
export const ZOOM_MIN = 0.15;
export const ZOOM_MAX = 4;

export type Bounds = { readonly minX: number; readonly minY: number; readonly maxX: number; readonly maxY: number };

/**
 * 让包围盒刚好装进 `width × height` 并居中（加载即自适应）。
 *
 * 居中项 `(width - k*boxWidth)/2` 不能省：省掉之后全图会贴左边，画面仍然「能看」，
 * 只有断言「中心点落在画布中心」才抓得到。空包围盒返回 null。
 */
export function fitTransform(bounds: Bounds | null, width: number, height: number): Transform | null {
  if (bounds === null) return null;
  const minX = bounds.minX - NODE_RADIUS - FIT_MARGIN;
  const maxX = bounds.maxX + NODE_RADIUS + FIT_MARGIN;
  const minY = bounds.minY - NODE_RADIUS - FIT_MARGIN;
  const maxY = bounds.maxY + NODE_RADIUS + NODE_LABEL_HEIGHT + FIT_MARGIN;
  const boxWidth = Math.max(maxX - minX, 1);
  const boxHeight = Math.max(maxY - minY, 1);
  const k = Math.max(Math.min(width / boxWidth, height / boxHeight, FIT_MAX_SCALE), FIT_MIN_SCALE);
  return {
    k,
    x: (width - k * boxWidth) / 2 - k * minX,
    y: (height - k * boxHeight) / 2 - k * minY,
  };
}

/**
 * 以 `(pointerX, pointerY)` 为不动点缩放。
 *
 * 「不动点」是这条公式的全部意义：光标下的那个人在缩放前后必须还在光标下，否则
 * 每次滚轮图都会从手底下滑走。断言方式是缩放前后用 `screenToWorld` 取同一个屏幕点，
 * 世界坐标必须相等。
 */
export function zoomAt(
  current: Transform,
  pointerX: number,
  pointerY: number,
  factor: number,
): Transform {
  const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, current.k * factor));
  const ratio = k / current.k;
  return {
    k,
    x: pointerX - (pointerX - current.x) * ratio,
    y: pointerY - (pointerY - current.y) * ratio,
  };
}

/** 屏幕坐标 → 世界坐标（`transform` 的逆）。`offsetX/offsetY` 是 SVG 在视口里的左上角。 */
export function screenToWorld(
  current: Transform,
  offsetX: number,
  offsetY: number,
  clientX: number,
  clientY: number,
): LayoutPoint {
  return {
    x: (clientX - offsetX - current.x) / current.k,
    y: (clientY - offsetY - current.y) / current.k,
  };
}

/** 世界坐标 → 屏幕坐标。fit 的断言用它验「中心落在画布中心」。 */
export function worldToScreen(
  current: Transform,
  world: LayoutPoint,
): LayoutPoint {
  return { x: current.x + current.k * world.x, y: current.y + current.k * world.y };
}

/**
 * 拖拽落点判定：离 `point` 最近、且在 `radius` 内、且不是 `excludeId` 的节点。
 * 命中就把两个人连起来，没命中就只当一次点击——半径写错会让「差一点没搭上」静默丢关系。
 */
export function nearestNodeId(
  layout: ReadonlyMap<string, LayoutPoint>,
  point: LayoutPoint,
  excludeId: string,
  radius: number,
): string | null {
  let found: string | null = null;
  let best = radius;
  for (const [id, candidate] of layout) {
    if (id === excludeId) continue;
    const distance = Math.hypot(candidate.x - point.x, candidate.y - point.y);
    if (distance < best) {
      best = distance;
      found = id;
    }
  }
  return found;
}
