/**
 * 画像治理（1.0 `app.js:941-949` / `:2034-2097`）。
 *
 * 数据来自 `GET /person-insights`：人物按核心度分三档，每档带当前生效的画像。
 * 反馈走 `POST /people/{id}/portrait-feedback`——**2.0 的请求体只有 `feedback`**
 * （`schemas/insights.py`），没有 1.0 的 `{revision_id, verdict, note}`，也没有
 * `PATCH /people/{id}/portrait`。所以这里只接「像他 / 不像他」（落成 feedback 文本），
 * **不提供锁定与编辑画像**——没有端点就不在界面上放一个点了会 405 的按钮。
 */

import { useCallback, useEffect, useState } from "react";

import {
  listPersonInsights,
  sendPortraitFeedback,
  type PersonInsightItem,
  type PersonInsightsResponse,
} from "../../shared/api/client";
import { describeReviewError } from "./reviewErrors";

const TIERS: readonly { key: "core" | "common" | "incidental"; label: string }[] = [
  { key: "core", label: "核心人物" },
  { key: "common", label: "常见人物" },
  { key: "incidental", label: "偶发人物" },
];

const LIKE = "像他";
const DISLIKE = "不像他";

interface Props {
  readonly scopeId: string;
}

export function PersonInsights({ scopeId }: Props) {
  const [data, setData] = useState<PersonInsightsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const apply = useCallback((next: PersonInsightsResponse) => {
    setData(next);
    setError(null);
  }, []);

  // 取数放在 effect 里、落地在 Promise 回调里：不产生一次多余的同步 setState 渲染。
  useEffect(() => {
    let cancelled = false;
    listPersonInsights(scopeId)
      .then((next) => {
        if (!cancelled) apply(next);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(describeReviewError(cause));
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [scopeId, apply]);

  async function feedback(item: PersonInsightItem, verdict: string) {
    setBusyId(item.person_id);
    setError(null);
    try {
      await sendPortraitFeedback(item.person_id, verdict);
      // 反馈写在服务端那一版上，重新取一遍才看到它的 feedback 字段。
      apply(await listPersonInsights(scopeId));
    } catch (cause) {
      setError(describeReviewError(cause));
    } finally {
      setBusyId(null);
    }
  }

  if (!loaded) {
    return <p className="empty">正在读取人物介绍…</p>;
  }

  return (
    <section className="people">
      {error === null ? null : (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {data !== null && TIERS.every((tier) => data.tiers[tier.key].length === 0) ? (
        <p className="empty">这个相册还没有可查看的人物介绍。</p>
      ) : null}

      {data === null
        ? null
        : TIERS.map((tier) => {
            const items = data.tiers[tier.key];
            if (items.length === 0) return null;
            return (
              <section key={tier.key} className="people-confirmed">
                <h3>
                  {tier.label} <small>· {items.length} 人</small>
                </h3>
                <div className="cluster-grid">
                  {items.map((item) => {
                    const portrait = item.portrait;
                    const currentFeedback = portrait?.feedback ?? null;
                    return (
                      <article className="cluster-card" key={item.person_id}>
                        <header className="cluster-head">
                          <div>
                            <p className="section-kicker">{tier.label}</p>
                            <strong>{item.display_name}</strong>
                          </div>
                          {portrait?.status === "user_locked" ? (
                            <span className="membership-badge family">已锁定</span>
                          ) : null}
                        </header>
                        <p className="cluster-meta">
                          {item.family_role ?? "未确认家庭角色"}
                          {typeof item.date_count === "number" ? ` · ${item.date_count} 天` : ""}
                          {typeof item.event_count === "number"
                            ? ` · ${item.event_count} 个事件`
                            : ""}
                        </p>
                        <div className="detail-empty">
                          {portrait === null
                            ? "还没有生成介绍。"
                            : `${portrait.portrait_text}${currentFeedback === null ? "" : `（你的反馈：${currentFeedback}）`}`}
                        </div>
                        {portrait === null ? null : (
                          <div className="cluster-actions">
                            <button
                              type="button"
                              className={currentFeedback === LIKE ? "primary" : "button ghost small"}
                              disabled={busyId === item.person_id}
                              onClick={() => void feedback(item, LIKE)}
                            >
                              像他
                            </button>
                            <button
                              type="button"
                              className={
                                currentFeedback === DISLIKE ? "primary" : "button ghost small"
                              }
                              disabled={busyId === item.person_id}
                              onClick={() => void feedback(item, DISLIKE)}
                            >
                              不像他
                            </button>
                          </div>
                        )}
                      </article>
                    );
                  })}
                </div>
              </section>
            );
          })}
    </section>
  );
}
