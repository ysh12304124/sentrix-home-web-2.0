/**
 * 家庭图谱的**唯一取数入口**。页面与各子组件都不自己 fetch，改数据一律走这里。
 *
 * `reload` 与 `refresh` 的差别不是命名洁癖：`reload` 是「用户换了相册 / 点了重试」，
 * 要亮 loading；`refresh` 是「刚写完一次，把服务端的新状态取回来」——**写完重拉但
 * 不闪 loading**，否则每改一次名字整页就白一下。
 *
 * **写回执先落本地、重拉在后台对账**（2026-11-19）：姓名、别名、家庭归属、确认身份的回执
 * 都带着已提交的值，关系的 PUT 回执带着正反两行、DELETE 意味着这对人之间不再有生效的边，
 * 所以界面不必等一份 200KB 的图谱回来才反应。`refresh` 在飞行中再被调用时只记一笔，
 * 等当前这次落地后补拉一次，不让多份请求叠在一起。
 *
 * loading 是**推导出来的**（请求键与已落地的结果键不一致），不是在 effect 开头
 * `setState("loading")`：后者是一次多余的级联渲染，也踩 oxlint 的 set-state-in-effect。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  getFamilyGraph,
  type FamilyPerson,
  type FamilyRelationship,
  type FamilyRun,
} from "../../../shared/api/client";
import { describeReviewError } from "../reviewErrors";

export type FamilyGraphState = "loading" | "ready" | "empty" | "error";

interface FamilyGraphData {
  state: FamilyGraphState;
  people: FamilyPerson[];
  relationships: FamilyRelationship[];
  run: FamilyRun | null;
  scopeIds: string[];
  error: string | null;
}

const INITIAL: FamilyGraphData = {
  state: "loading",
  people: [],
  relationships: [],
  run: null,
  scopeIds: [],
  error: null,
};

function toData(input: Awaited<ReturnType<typeof getFamilyGraph>>): FamilyGraphData {
  return {
    // 有没有人决定空不空；**关系条数不参与**——实测那份真实数据就是「8 人 0 关系」，
    // 它必须被当成有内容（计划书 §0），而不是空状态。
    state: input.people.length === 0 ? "empty" : "ready",
    people: input.people,
    relationships: input.relationships,
    run: input.run,
    scopeIds: input.scope_ids,
    error: null,
  };
}

/** 写回执里能直接落到本地的人物字段。 */
export type PersonPatch = Partial<Pick<FamilyPerson, "display_name" | "aliases" | "status" | "membership" | "hidden">>;

export interface UseFamilyGraph extends FamilyGraphData {
  reload: () => void;
  refresh: () => Promise<void>;
  /** 把回执里已提交的人物字段先写进本地，再在后台对账。 */
  applyPersonPatch: (id: string, patch: PersonPatch) => void;
  /**
   * 一对人之间的关系已被替换成 `rows`（DELETE 时为空）：本地先替换，再在后台对账。
   * 一条关系在库里是正反两行，所以按「无序的一对人」整体替换。
   */
  applyRelationships: (scopeId: string, a: string, b: string, rows: readonly FamilyRelationship[]) => void;
}

export function useFamilyGraph(scopeId: string): UseFamilyGraph {
  // 自增令牌：`reload()` 只是把它 +1；它和 scopeId 一起拼成请求键。
  const [token, setToken] = useState(0);
  const requestKey = `${scopeId}\u0000${token}`;
  // 落地的是「哪一次请求的结果」，不是一个裸数据——这样「正在加载」不用写 state 就能推出来。
  const latestLoad = useRef(0);
  const [result, setResult] = useState<{ key: string; data: FamilyGraphData } | null>(null);

  useEffect(() => {
    const load = ++latestLoad.current;
    let cancelled = false;
    const key = `${scopeId}\u0000${token}`;
    getFamilyGraph(scopeId)
      .then((graph) => {
        if (!cancelled && load === latestLoad.current) setResult({ key, data: toData(graph) });
      })
      .catch((cause: unknown) => {
        if (cancelled || load !== latestLoad.current) return;
        setResult((previous) => ({
          key,
          data: { ...(previous?.data ?? INITIAL), state: "error", error: describeReviewError(cause) },
        }));
      });
    return () => {
      cancelled = true;
    };
  }, [scopeId, token]);

  const reload = useCallback(() => setToken((current) => current + 1), []);

  // 正在飞行的那次重拉；飞行中又被要求重拉时只记 `dirty`，落地后补一次。
  const inflight = useRef<Promise<void> | null>(null);
  const dirty = useRef(false);

  const refreshOnce = useCallback(async () => {
    const load = ++latestLoad.current;
    const key = `${scopeId}\u0000${token}`;
    try {
      const graph = await getFamilyGraph(scopeId);
      if (load === latestLoad.current) setResult({ key, data: toData(graph) });
    } catch (cause: unknown) {
      if (load !== latestLoad.current) return;
      setResult((previous) => ({
        key,
        data: { ...(previous?.data ?? INITIAL), state: "error", error: describeReviewError(cause) },
      }));
    }
  }, [scopeId, token]);

  const refresh = useCallback((): Promise<void> => {
    if (inflight.current !== null) {
      dirty.current = true;
      return inflight.current;
    }
    const run = (async () => {
      try {
        do {
          dirty.current = false;
          await refreshOnce();
        } while (dirty.current);
      } finally {
        inflight.current = null;
      }
    })();
    inflight.current = run;
    return run;
  }, [refreshOnce]);

  const patchData = useCallback((change: (data: FamilyGraphData) => FamilyGraphData) => {
    // 写入之前就发出的重拉带回来的是旧值：作废它，免得回执刚落地又被旧数据盖回去一下。
    latestLoad.current += 1;
    setResult((previous) => previous === null ? null : { ...previous, data: change(previous.data) });
    // 回执只覆盖这一次写入；并发的其他改动靠后台对账带回来，不阻塞界面。
    void refresh();
  }, [refresh]);

  const applyPersonPatch = useCallback((id: string, patch: PersonPatch) => {
    patchData((data) => ({
      ...data,
      people: data.people.map((person) => person.id === id ? { ...person, ...patch } : person),
    }));
  }, [patchData]);

  const applyRelationships = useCallback(
    (scope: string, a: string, b: string, rows: readonly FamilyRelationship[]) => {
      const pair = (x: string, y: string) => (x < y ? `${x}\u0000${y}` : `${y}\u0000${x}`);
      const target = pair(a, b);
      patchData((data) => ({
        ...data,
        relationships: [
          ...data.relationships.filter(
            (row) => row.scope_id !== scope || pair(row.subject_entity_id, row.object_entity_id) !== target,
          ),
          ...rows,
        ],
      }));
    },
    [patchData],
  );

  const data =
    result === null || result.key !== requestKey
      ? { ...INITIAL }
      : result.data;

  return { ...data, reload, refresh, applyPersonPatch, applyRelationships };
}
