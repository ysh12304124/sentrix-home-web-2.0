import { describe, expect, it } from "vitest";

import type { FamilyPerson } from "../../../shared/api/client";
import {
  edgeKind,
  elderOf,
  FORCE,
  forceLayout,
  generations,
  radialLayout,
  relationDistances,
} from "./forceLayout";
import { NODE_RADIUS, type RelationEdge } from "./graphLayout";

function person(id: string, appearance = 1): FamilyPerson {
  return {
    id,
    scope_id: "album1",
    display_name: id,
    membership: null,
    portrait: null,
    avatar_face_instance_id: null,
    appearance_count: appearance,
    representative_media: [],
    status: "confirmed",
    aliases: [],
    hidden: false,
  };
}

function edge(subject: string, predicate: string, inverse: string, object: string): RelationEdge {
  return { subject_entity_id: subject, predicate, inverse_predicate: inverse, object_entity_id: object };
}

/** 一个三代之家 + 朋友 + 两个还没关系的人。 */
const PEOPLE = ["grandpa", "grandma", "dad", "mom", "kid", "kid2", "friend", "colleague", "loner_a", "loner_b"].map(
  (id, index) => person(id, 20 - index),
);
const EDGES = [
  edge("grandpa", "父亲", "儿子", "dad"),
  edge("grandma", "妈妈", "儿子", "dad"), // 口语称谓也要参与辈分
  edge("grandpa", "丈夫", "妻子", "grandma"),
  edge("dad", "丈夫", "妻子", "mom"),
  edge("kid", "儿子", "父亲", "dad"), // 去重可能留下反行：长辈在宾语位
  edge("mom", "母亲", "女儿", "kid2"),
  edge("dad", "朋友", "朋友", "friend"),
  edge("mom", "同事", "同事", "colleague"),
];

const distance = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);

describe("称谓归类", () => {
  it("长辈方向：正行、反行、口语称谓都认得", () => {
    expect(elderOf(edge("a", "父亲", "儿子", "b"))).toEqual({ elder: "a", younger: "b" });
    expect(elderOf(edge("b", "儿子", "父亲", "a"))).toEqual({ elder: "a", younger: "b" });
    expect(elderOf(edge("a", "外婆", "外孙", "b"))).toEqual({ elder: "a", younger: "b" });
    expect(elderOf(edge("a", "同事", "同事", "b"))).toBeNull();
  });

  it("连线类型：亲子 / 夫妻 / 其他", () => {
    expect(edgeKind(edge("a", "老婆", "老公", "b"))).toBe("partner");
    expect(edgeKind(edge("a", "儿子", "爸爸", "b"))).toBe("parent");
    expect(edgeKind(edge("a", "干妈", "干女儿", "b"))).toBe("other");
  });

  it("辈分：三代分三层，配偶与另一半同层，没有亲子关系的人没有辈分", () => {
    const levels = generations(PEOPLE, EDGES);
    expect(levels.get("grandpa")).toBe(0);
    expect(levels.get("grandma")).toBe(0);
    expect(levels.get("dad")).toBe(1);
    expect(levels.get("mom")).toBe(1);
    expect(levels.get("kid")).toBe(2);
    expect(levels.get("kid2")).toBe(2);
    expect(levels.has("friend")).toBe(false);
  });
});

describe("forceLayout 总览", () => {
  const layout = forceLayout(PEOPLE, EDGES);

  it("每个人都有坐标，包括还没有关系的人", () => {
    expect([...layout.keys()].sort()).toEqual(PEOPLE.map((item) => item.id).sort());
    for (const point of layout.values()) {
      expect(Number.isFinite(point.x) && Number.isFinite(point.y)).toBe(true);
    }
  });

  it("可复现：同样的数据排出同样的图", () => {
    expect([...forceLayout(PEOPLE, EDGES)]).toEqual([...layout]);
  });

  it("长辈在上：祖辈 < 父辈 < 子辈（y 越小越靠上）", () => {
    const y = (id: string) => layout.get(id)?.y ?? Number.NaN;
    expect(y("grandpa")).toBeLessThan(y("dad"));
    expect(y("grandma")).toBeLessThan(y("mom"));
    expect(y("dad")).toBeLessThan(y("kid"));
    expect(y("mom")).toBeLessThan(y("kid2"));
  });

  it("不重叠：任意两人的中心距不小于两个节点半径", () => {
    const points = [...layout.values()];
    for (let i = 0; i < points.length; i += 1) {
      for (let j = i + 1; j < points.length; j += 1) {
        expect(distance(points[i], points[j])).toBeGreaterThanOrEqual(NODE_RADIUS * 2);
      }
    }
  });

  it("夫妻比朋友离得近", () => {
    const at = (id: string) => layout.get(id) as { x: number; y: number };
    expect(distance(at("dad"), at("mom"))).toBeLessThan(distance(at("dad"), at("friend")));
  });

  it("拖过的人固定在松手的位置", () => {
    const pinned = forceLayout(PEOPLE, EDGES, { pins: new Map([["friend", { x: 900, y: -400 }]]) });
    expect(pinned.get("friend")).toEqual({ x: 900, y: -400 });
  });

  it("空图返回空结果", () => {
    expect(forceLayout([], []).size).toBe(0);
  });
});

describe("radialLayout 以某人为中心", () => {
  const layout = radialLayout("dad", PEOPLE, EDGES);
  const radius = (id: string) => distance(layout.get(id) as { x: number; y: number }, { x: 0, y: 0 });

  it("中心在原点，直接关系在第一圈，隔一层在第二圈，到不了的在最外圈", () => {
    expect(layout.get("dad")).toEqual({ x: 0, y: 0 });
    for (const id of ["grandpa", "grandma", "mom", "kid", "friend"]) expect(radius(id)).toBeCloseTo(FORCE.ringGap);
    for (const id of ["kid2", "colleague"]) expect(radius(id)).toBeCloseTo(FORCE.ringGap * 2);
    expect(radius("loner_a")).toBeCloseTo(FORCE.ringGap * 3);
  });

  it("关系距离按无向图计算", () => {
    const distances = relationDistances("kid2", EDGES);
    expect(distances.get("mom")).toBe(1);
    expect(distances.get("dad")).toBe(2);
    expect(distances.has("loner_a")).toBe(false);
  });

  it("焦点不在图里时返回空结果", () => {
    expect(radialLayout("nobody", PEOPLE, EDGES).size).toBe(0);
  });
});
