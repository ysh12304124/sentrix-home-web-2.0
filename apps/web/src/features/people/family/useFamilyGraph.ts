/**
 * 家庭图谱的**唯一取数入口**。页面与各子组件都不自己 fetch，改数据一律走这里。
 *
 * `reload` 与 `refresh` 的差别不是命名洁癖：`reload` 是「用户换了相册 / 点了重试」，
 * 要亮 loading；`refresh` 是「刚写完一次，把服务端的新状态取回来」——**写完重拉但
 * 不闪 loading**，否则每改一次名字整页就白一下。两者都**不做乐观更新**：后端是权威，
 * LWW / 锁语义在服务端，本地先改一份就是又抄了一遍规则。
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

export interface UseFamilyGraph extends FamilyGraphData {
  reload: () => void;
  refresh: () => Promise<void>;
}

export function useFamilyGraph(scopeId: string): UseFamilyGraph {
  // 自增令牌：`reload()` 只是把它 +1；它和 scopeId 一起拼成请求键。
  const [token, setToken] = useState(0);
  const requestKey = `${scopeId}\u0000${token}`;
  // 落地的是「哪一次请求的结果」，不是一个裸数据——这样「正在加载」不用写 state 就能推出来。
  const [result, setResult] = useState<{ key: string; data: FamilyGraphData } | null>(null);
  const readSequence = useRef(0);

  useEffect(() => {
    let cancelled = false;
    const sequence = ++readSequence.current;
    const key = `${scopeId}\u0000${token}`;
    getFamilyGraph(scopeId)
      .then((graph) => {
        if (!cancelled && sequence === readSequence.current) setResult({ key, data: toData(graph) });
      })
      .catch((cause: unknown) => {
        if (cancelled || sequence !== readSequence.current) return;
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

  const refresh = useCallback(async () => {
    const key = `${scopeId}\u0000${token}`;
    const sequence = ++readSequence.current;
    try {
      const graph = await getFamilyGraph(scopeId);
      if (sequence === readSequence.current) setResult({ key, data: toData(graph) });
    } catch (cause: unknown) {
      if (sequence !== readSequence.current) return;
      setResult((previous) => ({
        key,
        data: { ...(previous?.data ?? INITIAL), state: "error", error: describeReviewError(cause) },
      }));
    }
  }, [scopeId, token]);

  const data =
    result === null || result.key !== requestKey
      ? { ...INITIAL }
      : result.data;

  return { ...data, reload, refresh };
}
