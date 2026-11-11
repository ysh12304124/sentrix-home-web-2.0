import { useCallback, useEffect, useState } from "react";

import {
  batchConfirmPeople,
  confirmFaceCluster,
  listFaceClusters,
  listPeople,
  mergeFaceClusters,
  putFamilyRelationship,
  rejectFaceCluster,
  renamePerson,
  splitFaceCluster,
  type BatchConfirmItem,
  type FaceClusterSummary,
  type PersonPayload,
} from "../../shared/api/client";
import { BatchConfirmPanel, type BatchRelationDraft } from "./BatchConfirmPanel";
import { FaceClusterList } from "./FaceClusterList";
import type { ClusterRelationInput } from "./FaceClusterCard";
import { PersonList } from "./PersonList";
import { describeReviewError } from "./reviewErrors";

interface PeopleData {
  clusters: FaceClusterSummary[];
  people: PersonPayload[];
}

/**
 * 拉一次列表。**只取数据，不写 state**——写进哪里由调用方决定。
 *
 * 审阅页只管还没决定的那些簇；已确认的人走 ``/people``。两边的 scope 都由调用方给。
 */
async function fetchPeopleData(scopeId: string): Promise<PeopleData> {
  const [clusters, people] = await Promise.all([
    listFaceClusters(scopeId, "pending"),
    listPeople(scopeId, "confirmed"),
  ]);
  return { clusters, people };
}

/**
 * 人脸簇审阅页——S5 的产品硬约束（确认 / 拒绝 / 合并 / 拆分 / 命名）。
 *
 * 这个组件只做三件事：把服务端的判断摆出来、把用户的选择收起来、把请求发出去。
 * **不重算任何后端规则**：谁可审、是不是孤证都读 ``FaceClusterSummary`` 上的派生布尔；
 * 合并方向由用户在多选里指定，前端不做「哪个更像谁」的推断。
 */
export function PeopleView({
  scopeId,
  onConfirmSubmit,
  onConfirmResult,
}: {
  scopeId: string;
  onConfirmSubmit?: (clusterId: string) => void;
  onConfirmResult?: (clusterId: string, ok: boolean) => void;
}) {
  const [clusters, setClusters] = useState<FaceClusterSummary[]>([]);
  const [people, setPeople] = useState<PersonPayload[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [targetChoice, setTargetChoice] = useState<string | null>(null);
  const [batchMode, setBatchMode] = useState(false);
  const [batchSelected, setBatchSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const apply = useCallback((data: PeopleData) => {
    setClusters(data.clusters);
    setPeople(data.people);
  }, []);

  useEffect(() => {
    fetchPeopleData(scopeId)
      .then(apply)
      .catch((cause: unknown) => setError(describeReviewError(cause)))
      .finally(() => setLoaded(true));
  }, [scopeId, apply]);

  /**
   * 每个动作都走这一条：先清错误 → 跑 → 成功后重新拉一遍列表 → 失败把后端的 ``code``
   * 翻成中文摆到页面上。
   *
   * **重新拉而不是本地改状态**：member_count / revision / samples 都是服务端算的，
   * 本地改一份就是又抄了一遍规则，抄错的时候用户看到的簇和库里的是两回事。
   */
  async function run(action: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      await action();
      apply(await fetchPeopleData(scopeId));
      return true;
    } catch (cause) {
      setError(describeReviewError(cause));
      return false;
    } finally {
      setBusy(false);
    }
  }

  function toggleMerge(clusterId: string) {
    setSelected((current) => {
      if (current.includes(clusterId)) {
        return current.filter((id) => id !== clusterId);
      }
      // 最多两个：再选第三个时顶掉最早选的那个，而不是让点击静默失效。
      return [...current, clusterId].slice(-2);
    });
  }

  // 目标没显式选过（或选的那个已被取消勾选）时，默认取先点的那个。
  const mergeTarget =
    selected.length === 2
      ? selected.includes(targetChoice ?? "")
        ? (targetChoice as string)
        : selected[0]
      : null;
  const mergeSource =
    mergeTarget === null ? null : (selected.find((id) => id !== mergeTarget) ?? null);

  function handleMerge() {
    if (mergeTarget === null || mergeSource === null) {
      return;
    }
    const target = mergeTarget;
    const source = mergeSource;
    void run(async () => {
      await mergeFaceClusters(target, source);
      setSelected([]);
      setTargetChoice(null);
    });
  }

  function toggleBatch(clusterId: string) {
    setBatchSelected((current) =>
      current.includes(clusterId)
        ? current.filter((id) => id !== clusterId)
        : [...current, clusterId],
    );
  }

  function exitBatch() {
    setBatchMode(false);
    setBatchSelected([]);
  }

  /**
   * 批量确认 → 用回执里的 ``entity_id`` 建关系。
   *
   * **关系行按 person_id 记，不按姓名**：两个人可能同名，姓名映射会把关系连到错的人。
   * 失败逐件回执，成功的确认照常保留；关系只在两端都确认成功时才建。
   */
  async function submitBatch(
    items: BatchConfirmItem[],
    relations: BatchRelationDraft[],
  ): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      const result = await batchConfirmPeople(items);
      const entityByPerson = new Map(
        result.confirmed.map((row) => [row.person_id, row.entity_id]),
      );
      const relationErrors: string[] = [];
      for (const relation of relations) {
        const subject = entityByPerson.get(relation.subjectPersonId);
        const object = entityByPerson.get(relation.objectPersonId);
        if (subject === undefined || object === undefined) continue;
        try {
          await putFamilyRelationship({
            scopeId,
            subjectEntityId: subject,
            predicate: relation.predicate,
            objectEntityId: object,
            inversePredicate: relation.inversePredicate,
          });
        } catch (cause) {
          relationErrors.push(describeReviewError(cause));
        }
      }

      apply(await fetchPeopleData(scopeId));
      const failures = result.failed.map(
        (row) => `「${row.name || row.person_id}」确认失败：${row.error}`,
      );
      const messages = [...failures, ...relationErrors];
      if (messages.length > 0) {
        setError(messages.join("；"));
        // 有失败时保留勾选，用户能看清哪些没成功、再试一次。
        return false;
      }
      exitBatch();
      return true;
    } catch (cause) {
      setError(describeReviewError(cause));
      return false;
    } finally {
      setBusy(false);
    }
  }

  function handleConfirm(
    clusterId: string,
    name: string,
    familyRole: string | null,
    relation: ClusterRelationInput | null,
  ): Promise<boolean> {
    onConfirmSubmit?.(clusterId);
    return run(async () => {
      const confirmed = await confirmFaceCluster(clusterId, { name, family_role: familyRole });
      if (relation !== null) {
        await putFamilyRelationship({
          scopeId,
          subjectEntityId: confirmed.entity.id,
          predicate: relation.predicate,
          objectEntityId: relation.targetId,
          inversePredicate: relation.inversePredicate,
        });
      }
    }).then((ok) => {
      onConfirmResult?.(clusterId, ok);
      return ok;
    });
  }

  return (
    <section className="people">
      <header className="people-head">
        <div>
          <p className="section-kicker">人物</p>
          <h2>待确认的人物</h2>
        </div>
        <span className="result-count">{clusters.length} 位待确认</span>
      </header>
      <p className="people-lead">
        这些是系统从照片里自动整理出的面孔。确认会给它一个名字；暂时跳过不会改动原始照片。
      </p>

      <div className="cluster-actions">
        <button
          type="button"
          className="button ghost small"
          disabled={busy || clusters.length === 0}
          onClick={() => {
            if (batchMode) exitBatch();
            else setBatchMode(true);
          }}
        >
          {batchMode ? "退出批量确认" : "批量确认"}
        </button>
      </div>

      {error === null ? null : (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {!loaded ? (
        <p className="empty">正在读取待确认的人物…</p>
      ) : (
        <>
          {batchMode && batchSelected.length > 0 ? (
            <BatchConfirmPanel
              clusters={clusters.filter((cluster) => batchSelected.includes(cluster.id))}
              busy={busy}
              onSubmit={submitBatch}
              onCancel={exitBatch}
            />
          ) : null}

          <FaceClusterList
            clusters={clusters}
            people={people}
            selectedIds={selected}
            mergeTargetId={mergeTarget}
            batchMode={batchMode}
            batchSelectedIds={batchSelected}
            busy={busy}
            onToggleMerge={toggleMerge}
            onChooseMergeTarget={setTargetChoice}
            onToggleBatch={toggleBatch}
            onMerge={handleMerge}
            onConfirm={handleConfirm}
            onReject={(clusterId: string) => {
              void run(() => rejectFaceCluster(clusterId));
            }}
            onSplit={(clusterId: string, faceInstanceId: string) => {
              void run(() => splitFaceCluster(clusterId, faceInstanceId));
            }}
          />

          <section className="people-confirmed">
            <h3>已确认的人物</h3>
            <PersonList
              people={people}
              busy={busy}
              onRename={(personId: string, name: string) => run(() => renamePerson(personId, name))}
            />
          </section>
        </>
      )}
    </section>
  );
}
