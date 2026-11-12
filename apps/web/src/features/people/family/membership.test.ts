/// <reference types="node" />
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { myRoleOptions, RELATION_INVERSES, RELATION_ROLE_GROUPS, relationFromSelf } from "./membership";

/**
 * 前端的称谓表是后端 `RELATION_INVERSES` 的一份副本（为了提交前只给合法组合）。
 * 这里直接读后端源文件逐项比对：任何一边改了词表而另一边没跟，这条测试就会失败。
 */
function backendInverses(): Record<string, string[]> {
  // `vitest` 的工作目录是 `apps/web`（同 `source-hygiene.test.ts`）。
  const source = readFileSync(
    join(process.cwd(), "../api/src/sentrix/domain/identity/family_graph.py"),
    "utf8",
  );
  const block = source.slice(source.indexOf("RELATION_INVERSES"), source.indexOf("\n}\n"));
  const table: Record<string, string[]> = {};
  for (const match of block.matchAll(/"([^"]+)":\s*\{([^}]*)\}/g)) {
    table[match[1]] = [...match[2].matchAll(/"([^"]+)"/g)].map((item) => item[1]).sort();
  }
  return table;
}

describe("关系称谓表", () => {
  it("与后端 RELATION_INVERSES 逐项一致", () => {
    const frontend = Object.fromEntries(
      Object.entries(RELATION_INVERSES).map(([key, values]) => [key, [...values].sort()]),
    );
    const backend = backendInverses();
    expect(Object.keys(backend).length).toBeGreaterThan(20);
    expect(frontend).toEqual(backend);
  });

  it("下拉里的每个称谓都有合法的反向，且反向选项都被后端接受", () => {
    for (const group of RELATION_ROLE_GROUPS) {
      for (const role of group.roles) {
        const options = myRoleOptions(role);
        expect(options.length, role).toBeGreaterThan(0);
        for (const option of options) expect(RELATION_INVERSES[role]).toContain(option);
      }
    }
  });

  it("兄弟姐妹按长幼收窄：对方是哥哥时我只能是弟弟或妹妹", () => {
    expect(myRoleOptions("哥哥")).toEqual(["弟弟", "妹妹"]);
    expect(myRoleOptions("妹妹")).toEqual(["哥哥", "姐姐"]);
  });
});

describe("relationFromSelf", () => {
  const edge = { subject_entity_id: "dad", predicate: "父亲", object_entity_id: "son", inverse_predicate: "儿子" };

  it("我是主语时，对方的称谓是反向谓词", () => {
    expect(relationFromSelf(edge, "dad")).toEqual({ otherId: "son", otherRole: "儿子", selfRole: "父亲" });
  });

  it("我是宾语时，对方的称谓是正向谓词", () => {
    expect(relationFromSelf(edge, "son")).toEqual({ otherId: "dad", otherRole: "父亲", selfRole: "儿子" });
  });
});
