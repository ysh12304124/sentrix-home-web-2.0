import type {
  FaceClusterSummary,
  PersonPayload,
} from "../../shared/api/client";
import { FaceClusterCard, type ClusterRelationInput } from "./FaceClusterCard";
import { clusterDisplayName } from "./reviewErrors";

export interface FaceClusterListProps {
  clusters: FaceClusterSummary[];
  /** 已确认的人物——确认簇时可选的关系目标。 */
  people: PersonPayload[];
  /** 已勾选用于合并的簇 id，最多两个。 */
  selectedIds: string[];
  /** 两个里保留哪一个（另一个作为来源并进来）。 */
  mergeTargetId: string | null;
  batchMode: boolean;
  batchSelectedIds: string[];
  busy: boolean;
  onToggleMerge: (clusterId: string) => void;
  onChooseMergeTarget: (clusterId: string) => void;
  onToggleBatch: (clusterId: string) => void;
  onMerge: () => void;
  onConfirm: (
    clusterId: string,
    name: string,
    familyRole: string | null,
    relation: ClusterRelationInput | null,
  ) => Promise<boolean>;
  onReject: (clusterId: string) => void;
  onSplit: (clusterId: string, faceInstanceId: string) => void;
}

/**
 * 待审簇列表。
 *
 * 孤证（``single_sample``）单独折进 ``<details>``：这是 1.0 的处理方式
 * （``app.js:679`` 把 ``member_count < 2`` 的簇收起来），理由是它们多半是小脸或误检，
 * 混在正常簇里会让「确认身份」这件事的默认判断被稀释。判据仍来自服务端字段。
 */
export function FaceClusterList({
  clusters,
  people,
  selectedIds,
  mergeTargetId,
  batchMode,
  batchSelectedIds,
  busy,
  onToggleMerge,
  onChooseMergeTarget,
  onToggleBatch,
  onMerge,
  onConfirm,
  onReject,
  onSplit,
}: FaceClusterListProps) {
  const multi = clusters.filter((cluster) => !cluster.single_sample);
  const single = clusters.filter((cluster) => cluster.single_sample);
  const pair = selectedIds.length === 2 ? selectedIds : null;

  function card(cluster: FaceClusterSummary) {
    return (
      <FaceClusterCard
        key={cluster.id}
        cluster={cluster}
        people={people}
        selectedForMerge={selectedIds.includes(cluster.id)}
        batchMode={batchMode}
        selectedForBatch={batchSelectedIds.includes(cluster.id)}
        busy={busy}
        onToggleMerge={onToggleMerge}
        onToggleBatch={onToggleBatch}
        onConfirm={onConfirm}
        onReject={onReject}
        onSplit={onSplit}
      />
    );
  }

  return (
    <>
      {pair === null ? null : (
        <section className="merge-bar" aria-label="合并两位人物">
          <h3>合并两位人物</h3>
          <p className="merge-hint">
            选一个<strong>保留</strong>为目标，另一个人的样本会并进它。合并方向由你定，
            后端不做推断。
          </p>
          <ul className="merge-choices">
            {pair.map((id) => {
              const cluster = clusters.find((item) => item.id === id);
              if (cluster === undefined) {
                return null;
              }
              return (
                <li key={id}>
                  <label>
                    <input
                      type="radio"
                      name="merge-target"
                      checked={mergeTargetId === id}
                      onChange={() => onChooseMergeTarget(id)}
                    />
                    {clusterDisplayName(cluster)} · {cluster.member_count} 张样本
                  </label>
                </li>
              );
            })}
          </ul>
          <button type="button" className="primary" disabled={busy} onClick={onMerge}>
            合并这两位
          </button>
        </section>
      )}

      {clusters.length === 0 ? (
        <p className="empty">没有待确认的人物。</p>
      ) : (
        <div className="cluster-grid">{multi.map(card)}</div>
      )}

      {single.length > 0 ? (
        <details className="single-clusters">
          <summary>
            单张样本（{single.length}）· 可能是小脸或误检，展开后谨慎确认
          </summary>
          <div className="cluster-grid">{single.map(card)}</div>
        </details>
      ) : null}
    </>
  );
}
