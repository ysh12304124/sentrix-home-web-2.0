import { describe, expect, it } from "vitest";

import type { FamilyPerson } from "../../../shared/api/client";
import { matchPerson, personTier, splitByTier } from "./strangers";

function person(overrides: Partial<FamilyPerson> = {}): FamilyPerson {
  return {
    id: "p",
    scope_id: "album1",
    display_name: "未确认姓名人物",
    membership: null,
    portrait: null,
    avatar_face_instance_id: null,
    appearance_count: 1,
    representative_media: [],
    status: "pending",
    aliases: [],
    hidden: false,
    ...overrides,
  };
}

describe("personTier", () => {
  it("已确认的人出现一次也是「我的人物」，不被折叠", () => {
    expect(personTier(person({ status: "confirmed", display_name: "外婆", appearance_count: 1 }))).toBe("known");
  });

  it("待确认且出现 ≥ 3 次是「可能认识」，≤ 2 次是「偶遇面孔」", () => {
    expect(personTier(person({ appearance_count: 3 }))).toBe("likely");
    expect(personTier(person({ appearance_count: 2 }))).toBe("passerby");
  });

  it("待确认但用户已起名的，次数少也算「可能认识」", () => {
    expect(personTier(person({ display_name: "王王", appearance_count: 1 }))).toBe("likely");
  });

  it("标为路人优先于一切：确认过、出现很多次也收进偶遇面孔", () => {
    expect(personTier(person({ status: "confirmed", display_name: "邻居", appearance_count: 9, hidden: true }))).toBe(
      "passerby",
    );
  });

  it("按真实形状分层：8 个已确认、5 个常见、42 个偶遇", () => {
    const people = [
      ...Array.from({ length: 8 }, (_, i) => person({ id: `k${i}`, status: "confirmed", display_name: `人${i}`, appearance_count: 6 })),
      ...Array.from({ length: 5 }, (_, i) => person({ id: `l${i}`, appearance_count: 3 + i })),
      ...Array.from({ length: 42 }, (_, i) => person({ id: `s${i}`, appearance_count: 1 + (i % 2) })),
    ];
    const tiers = splitByTier(people);
    expect([tiers.known.length, tiers.likely.length, tiers.passerby.length]).toEqual([8, 5, 42]);
  });
});

describe("matchPerson", () => {
  it("名字命中返回 null，别名命中返回那个别名，都不命中返回 undefined", () => {
    const mom = person({ display_name: "妈妈", aliases: ["阿娟", "老妈"] });
    expect(matchPerson(mom, "妈妈")).toBeNull();
    expect(matchPerson(mom, "阿娟")).toBe("阿娟");
    expect(matchPerson(mom, "爸")).toBeUndefined();
    expect(matchPerson(mom, "  ")).toBeNull();
  });
});
