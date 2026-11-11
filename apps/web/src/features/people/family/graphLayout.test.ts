/**
 * 布局单测。布局是纯函数，这些断言直接钉住计划书 §5 要的几条：
 * 分层正确性、**待归类必须出现在结果里**、空图不炸、一对人多条边只出一条。
 *
 * 假件按契约的完整字段造，不省字段——省字段的假件比真实契约宽松，前端漏读一个字段
 * 它也能过，那种红灯要留到真后端上才亮（`PeopleView.test.tsx` 的同一条理由）。
 */

import { describe, expect, it } from "vitest";

import type { FamilyPerson, FamilyRelationship } from "../../../shared/api/client";
import {
  dedupeRelationships,
  familyLevels,
  FIT_MARGIN,
  fitTransform,
  graphLayout,
  layoutBounds,
  nearestNodeId,
  NODE_LABEL_HEIGHT,
  NODE_RADIUS,
  screenToWorld,
  worldToScreen,
  zoomAt,
  ZOOM_MAX,
  ZOOM_MIN,
} from "./graphLayout";

const STUB = {
  scope_id: "album1",
  avatar_face_instance_id: null,
  representative_media: [] as FamilyPerson["representative_media"],
};

function person(id: string, overrides: Partial<FamilyPerson> = {}): FamilyPerson {
  return {
    ...STUB,
    id,
    display_name: id,
    membership: null,
    portrait: null,
    appearance_count: 1,
    ...overrides,
  };
}

function member(id: string, membership: "family" | "friend" | "unknown"): FamilyPerson {
  return person(id, {
    membership: {
      id: `m_${id}`,
      scope_id: "album1",
      entity_id: id,
      membership,
      source: "model",
      confidence: 0.5,
      evidence_refs: [],
      inference_run_id: null,
      state: "active",
      locked: false,
      revision: 1,
      supersedes_id: null,
      created_at: "2026-10-02T00:00:00+00:00",
      updated_at: "2026-10-02T00:00:00+00:00",
    },
  });
}

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

describe("familyLevels", () => {
  it("辈分传播：object 比 subject 低一层，方向不翻转", () => {
    const people = [member("dad", "family"), member("son", "family")];
    const levels = familyLevels(people, [relation("dad", "父亲", "儿子", "son")]);
    expect(levels.get("dad")).toBe(0);
    expect(levels.get("son")).toBe(1);
  });

  it("只有 1.0 那六个长辈谓词参与传播，叔伯不在内", () => {
    const people = [member("uncle", "family"), member("nephew", "family")];
    const levels = familyLevels(people, [relation("uncle", "叔叔", "侄子", "nephew")]);
    // 叔叔不在 parentRelations 里，两个人都留在第 0 层——这是 1.0 的原样行为
    expect(levels.get("uncle")).toBe(0);
    expect(levels.get("nephew")).toBe(0);
  });
});

describe("graphLayout", () => {
  it("父亲画在儿子上一层（y 更小）", () => {
    const people = [member("dad", "family"), member("son", "family")];
    const layout = graphLayout(people, [relation("dad", "父亲", "儿子", "son")], 900, 520);
    const dad = layout.get("dad");
    const son = layout.get("son");
    expect(dad).toBeDefined();
    expect(son).toBeDefined();
    expect(dad!.y).toBeLessThan(son!.y);
    // 相邻两层：中间没有别人，差正好是一个行距
    expect(son!.y - dad!.y).toBeGreaterThan(0);
  });

  it("待归类（unknown 与 null）必须出现在布局结果里，且排在家人的下面", () => {
    const people = [
      member("dad", "family"),
      member("son", "family"),
      member("friend", "friend"),
      member("unknown_row", "unknown"),
      person("never_inferred"), // membership === null：与 unknown 同一件事
    ];
    const layout = graphLayout(people, [relation("dad", "父亲", "儿子", "son")], 900, 520);

    expect(layout.size).toBe(5);
    const familyY = layout.get("son")!.y;
    // friend 与两个 unknown 都在家人层之下
    expect(layout.get("friend")!.y).toBeGreaterThan(familyY);
    expect(layout.get("unknown_row")!.y).toBeGreaterThan(layout.get("friend")!.y);
    expect(layout.get("never_inferred")!.y).toBeGreaterThan(layout.get("friend")!.y);
  });

  it("空图不炸：没有人生成空 Map，包围盒为 null", () => {
    const layout = graphLayout([], [], 900, 520);
    expect(layout.size).toBe(0);
    expect(layoutBounds(layout)).toBeNull();
  });

  it("人多了不重叠：同一行的相邻节点中心距不小于最小列距", () => {
    const people = Array.from({ length: 12 }, (_, index) => member(`p${index}`, "family"));
    const layout = graphLayout(people, [], 900, 520);
    const xs = [...layout.values()].map((point) => point.x).sort((a, b) => a - b);
    for (let index = 1; index < xs.length; index += 1) {
      expect(xs[index] - xs[index - 1]).toBeGreaterThanOrEqual(124);
    }
  });
});

// 视图变换的数学。这些公式原先埋在 `FamilyGraphView` 的事件处理里，一行测试都没有——
// 写错只会让画面错位（贴边、缩放漂移、拖拽搭不上），不会抛错，所以必须定点断言。

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
