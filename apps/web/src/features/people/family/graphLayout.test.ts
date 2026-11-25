/**
 * 图的公共几何单测：去重、自适应、缩放不动点、坐标互逆、落点判定。
 * 节点坐标本身的性质在 `forceLayout.test.ts`。
 *
 * 假件按契约的完整字段造，不省字段——省字段的假件比真实契约宽松，前端漏读一个字段
 * 它也能过，那种红灯要留到真后端上才亮。
 */

import { describe, expect, it } from "vitest";

import type { FamilyRelationship } from "../../../shared/api/client";
import {
  dedupeRelationships,
  FIT_MARGIN,
  fitTransform,
  nearestNodeId,
  NODE_LABEL_HEIGHT,
  NODE_RADIUS,
  screenToWorld,
  worldToScreen,
  zoomAt,
  ZOOM_MAX,
  ZOOM_MIN,
} from "./graphLayout";

function relation(
  subject: string,
  predicate: string,
  inverse: string,
  object: string,
): FamilyRelationship {
  return {
    id: `r_${subject}_${object}`,
    scope_id: "album1",
    family_graph_id: null,
    subject_entity_id: subject,
    predicate,
    object_entity_id: object,
    inverse_predicate: inverse,
    source: "model",
    confidence: 0.5,
    evidence_refs: [],
    state: "active",
    locked: false,
    revision: 1,
  };
}

describe("dedupeRelationships", () => {
  it("一对人多条边只出一条，优先级小者胜", () => {
    // 顺序刻意让**最后一条不是胜者**：只按「保留最后一条」实现的版本会给出「朋友」，
    // 于是这条断言能杀掉那个变异；把胜者放最后则两种实现同解，等于没测。
    const edges = dedupeRelationships([
      relation("son", "儿子", "父亲", "dad"),
      relation("dad", "父亲", "儿子", "son"),
      relation("dad", "朋友", "朋友", "son"),
    ]);
    expect(edges).toHaveLength(1);
    // 父亲(1) 胜过 朋友(8)；方向保留 subject=长辈
    expect(edges[0].predicate).toBe("父亲");
    expect(edges[0].subject_entity_id).toBe("dad");
    expect(edges[0].object_entity_id).toBe("son");
  });

  it("同优先级按谓词字典序决胜——不靠插入顺序", () => {
    const forward = dedupeRelationships([
      relation("a", "母亲", "儿子", "b"),
      relation("a", "父亲", "儿子", "b"),
    ]);
    const backward = dedupeRelationships([
      relation("a", "父亲", "儿子", "b"),
      relation("a", "母亲", "儿子", "b"),
    ]);
    // 按 UTF-16 码位比较：母(U+6BCD) < 父(U+7236)，所以「母亲」胜出，
    // 与两条边的插入顺序无关。
    expect(forward[0].predicate).toBe("母亲");
    expect(backward[0].predicate).toBe("母亲");
  });
});

describe("fitTransform", () => {
  const WIDTH = 960;
  const HEIGHT = 560;

  it("空包围盒返回 null——没有节点时不产生一个 NaN 变换", () => {
    expect(fitTransform(null, WIDTH, HEIGHT)).toBeNull();
  });

  it("包围盒在画布上居中：左右、上下留白相等（居中项不能省）", () => {
    const bounds = { minX: -200, minY: -60, maxX: 200, maxY: 60 };
    const transform = fitTransform(bounds, WIDTH, HEIGHT);
    expect(transform).not.toBeNull();
    // 断言用「被适配的那个盒子」而不是节点中心盒：适配盒在下方多留了名字的高度，
    // 居中是对它成立的。常量从 graphLayout 导出，不在这里重写一遍数字。
    const fitted = {
      minX: bounds.minX - NODE_RADIUS - FIT_MARGIN,
      maxX: bounds.maxX + NODE_RADIUS + FIT_MARGIN,
      minY: bounds.minY - NODE_RADIUS - FIT_MARGIN,
      maxY: bounds.maxY + NODE_RADIUS + NODE_LABEL_HEIGHT + FIT_MARGIN,
    };
    const topLeft = worldToScreen(transform!, { x: fitted.minX, y: fitted.minY });
    const bottomRight = worldToScreen(transform!, { x: fitted.maxX, y: fitted.maxY });
    // 左右/上下留白相等 = 整体居中；漏掉居中项时 left/top 会变成 0，这条就红。
    expect(topLeft.x).toBeCloseTo(WIDTH - bottomRight.x, 6);
    expect(topLeft.y).toBeCloseTo(HEIGHT - bottomRight.y, 6);
    // 居中的另一个等价说法：适配盒的中点落在画布中心。
    expect((topLeft.x + bottomRight.x) / 2).toBeCloseTo(WIDTH / 2, 6);
    expect((topLeft.y + bottomRight.y) / 2).toBeCloseTo(HEIGHT / 2, 6);
  });

  it("单点图落在画布中心，而不是贴左边", () => {
    // 单个节点的包围盒是它自己；它的中心必须映射到画布中心。
    const transform = fitTransform({ minX: 0, minY: 0, maxX: 0, maxY: 0 }, WIDTH, HEIGHT)!;
    const screen = worldToScreen(transform, { x: 0, y: 0 });
    // y 方向上包围盒下沿为名字留了高度，所以只断言 x 的中心；y 用「不出界」兜。
    expect(screen.x).toBeCloseTo(WIDTH / 2, 6);
    expect(screen.y).toBeGreaterThan(0);
    expect(screen.y).toBeLessThan(HEIGHT);
  });

  it("全图不出界，且缩放不超过上限", () => {
    const bounds = { minX: 0, minY: 0, maxX: 900, maxY: 40 };
    const transform = fitTransform(bounds, WIDTH, HEIGHT)!;
    const topLeft = worldToScreen(transform, { x: bounds.minX, y: bounds.minY });
    const bottomRight = worldToScreen(transform, { x: bounds.maxX, y: bounds.maxY });
    expect(topLeft.x).toBeGreaterThanOrEqual(-0.001);
    expect(topLeft.y).toBeGreaterThanOrEqual(-0.001);
    expect(bottomRight.x).toBeLessThanOrEqual(WIDTH + 0.001);
    expect(bottomRight.y).toBeLessThanOrEqual(HEIGHT + 0.001);
    expect(transform.k).toBeLessThanOrEqual(1.4);
  });
});

describe("zoomAt", () => {
  it("光标下的世界坐标在缩放前后不变（不动点）", () => {
    const before = { k: 1, x: 30, y: -12 };
    const pointer = { x: 300, y: 220 };
    const worldBefore = screenToWorld(before, 0, 0, pointer.x, pointer.y);

    const after = zoomAt(before, pointer.x, pointer.y, 2.5);
    const worldAfter = screenToWorld(after, 0, 0, pointer.x, pointer.y);

    expect(after.k).toBeCloseTo(2.5, 6);
    expect(worldAfter.x).toBeCloseTo(worldBefore.x, 6);
    expect(worldAfter.y).toBeCloseTo(worldBefore.y, 6);
  });

  it("缩小同样保持不动点", () => {
    const before = { k: 2, x: -100, y: 40 };
    const pointer = { x: 120, y: 90 };
    const worldBefore = screenToWorld(before, 0, 0, pointer.x, pointer.y);
    const after = zoomAt(before, pointer.x, pointer.y, 0.4);
    const worldAfter = screenToWorld(after, 0, 0, pointer.x, pointer.y);
    expect(worldAfter.x).toBeCloseTo(worldBefore.x, 6);
    expect(worldAfter.y).toBeCloseTo(worldBefore.y, 6);
  });

  it("缩放比例被夹在上下限之间", () => {
    const base = { k: 1, x: 0, y: 0 };
    expect(zoomAt(base, 0, 0, 1000).k).toBe(ZOOM_MAX);
    expect(zoomAt(base, 0, 0, 0.00001).k).toBe(ZOOM_MIN);
  });
});

describe("screenToWorld / worldToScreen", () => {
  it("互为逆变换（含偏移量）", () => {
    const transform = { k: 2, x: 10, y: 20 };
    const world = screenToWorld(transform, 5, 7, 115, 227);
    expect(world.x).toBeCloseTo(50, 6);
    expect(world.y).toBeCloseTo(100, 6);
    const back = worldToScreen(transform, world);
    expect(back.x).toBeCloseTo(115 - 5, 6);
    expect(back.y).toBeCloseTo(227 - 7, 6);
  });
});

describe("nearestNodeId", () => {
  const layout = new Map([
    ["a", { x: 0, y: 0 }],
    ["b", { x: 100, y: 0 }],
  ]);

  it("命中半径内最近的节点", () => {
    expect(nearestNodeId(layout, { x: 10, y: 0 }, "none", 40)).toBe("a");
    expect(nearestNodeId(layout, { x: 88, y: 0 }, "none", 40)).toBe("b");
  });

  it("排除自己在内——拖拽不能连到自己", () => {
    expect(nearestNodeId(layout, { x: 0, y: 0 }, "a", 40)).toBeNull();
    expect(nearestNodeId(layout, { x: 4, y: 0 }, "a", 40)).toBeNull();
  });

  it("半径之外返回 null——松手处没人才算点击而不是建关系", () => {
    expect(nearestNodeId(layout, { x: 50, y: 300 }, "none", 40)).toBeNull();
  });
});
