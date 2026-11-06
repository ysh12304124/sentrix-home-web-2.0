/**
 * 人物与关系。对应 1.0 `src/app.js:648` 的 `peopleView()`。
 *
 * 三个 tab：**名录（默认） / 关系图谱 / 待确认簇**。默认是名录不是图谱，依据是实测：
 * 刚导入的相册跑出来的图是 8 个孤点（计划书 §0）；1.0 的默认恰恰是图谱，这是 F7
 * 要改的第一件事。
 *
 * **不加新路由**：人物详情走 `?person=<id>`（计划书 §1 裁决③）。理由很实在——
 * `app/router.test.tsx:51` 钉死「路由数 == 导航项数 == 页面文件数」，加一条参数路由
 * 就要动那条门禁；查询参数同样能刷新保持、能后退、能分享，代价为零。
 */

import { useState } from "react";

import { newOperationId, reportClientEvent } from "../../shared/clientEvents";
import { useSearchParams } from "react-router";

import { PageHeader } from "../../shared/ui/PageHeader";
import { Section } from "../../shared/ui/Section";
import { useScope } from "../../app/ScopeProvider";
import { FamilyGraphView } from "./family/FamilyGraphView";
import { FamilyRoster } from "./family/FamilyRoster";
import { confirmedRelationships } from "./family/graphLayout";
import { PersonDetail } from "./family/PersonDetail";
import { personDisplayName } from "./family/membership";
import { useFamilyGraph } from "./family/useFamilyGraph";
import { PeopleView } from "./PeopleView";
import { PersonInsights } from "./PersonInsights";

type View = "roster" | "graph" | "review" | "insights";

const TABS: readonly { value: View; label: string }[] = [
  { value: "roster", label: "人物" },
  { value: "graph", label: "关系" },
  { value: "review", label: "待确认的人物" },
  { value: "insights", label: "人物介绍" },
];

export function PeoplePage() {
  const { scopeId } = useScope();
  const { state, people, relationships, error, refresh } = useFamilyGraph(scopeId);
  const [view, setView] = useState<View>("roster");
  const [searchParams, setSearchParams] = useSearchParams();
  const [relateTo, setRelateTo] = useState<string | null>(null);

  /**
   * **一条关系在库里是两行**（正向 + 反向）。后端 `set_relationship` 一次写两行，
   * `GET /family-graph` 原样返回，所以直接数 `relationships.length` 会把「1 条」
   * 显示成「2 条」。布局与详情面板都走了 `dedupeRelationships`，这里对齐它们。
   */
  const edgeCount = confirmedRelationships(relationships).length;
  const selectedId = searchParams.get("person");
  const selected = people.find((person) => person.id === selectedId) ?? null;

  /**
   * 改 `?person=`。
   *
   * **以 `window.location.search` 为准重建参数**，不是在 `searchParams` 上增删：
   * `ScopeProvider` 切相册时用 `history.replaceState` 直接写 URL，react-router 收不到
   * 那次变更，它的内部 location 里 `scope` 可能还是旧值——直接改 router 的参数会把
   * 用户刚切的相册覆盖掉。以窗口地址为准就没有这个问题。
   */
  function changeView(next: View) {
    setView(next);
    void reportClientEvent({
      eventName: "people.view",
      result: "ok",
      operationId: newOperationId(),
      scopeId,
    });
  }

  function selectPerson(personId: string | null, relate: string | null = null) {
    if (personId !== null) {
      void reportClientEvent({
        eventName: "people.detail_open",
        result: "ok",
        operationId: newOperationId(),
        scopeId,
        entityId: personId,
      });
    }
    setRelateTo(relate);
    const next = new URLSearchParams(window.location.search);
    if (personId === null) next.delete("person");
    else next.set("person", personId);
    setSearchParams(next);
  }

  const banner = state === "error" ? error : null;

  return (
    <>
      <PageHeader
        kicker="家庭人物"
        title="人物与关系"
        description="系统会先整理照片里的人；你确认或修改的内容会优先保留，不会被后续整理覆盖。"
      />

      <div className="family-tabs" role="tablist" aria-label="人物视图">
        {TABS.map((tab) => (
          <button
            key={tab.value}
            type="button"
            role="tab"
            aria-selected={view === tab.value}
            className={view === tab.value ? "family-tab active" : "family-tab"}
            onClick={() => changeView(tab.value)}
          >
            {tab.label}
          </button>
        ))}
        <span className="family-tabs-note">
          {state === "loading" ? "正在读取…" : `${people.length} 人 · ${edgeCount} 条已确认关系`}
        </span>
      </div>

      {banner === null ? null : (
        <p className="error" role="alert">
          {banner}
        </p>
      )}

      {view === "roster" ? (
        <Section kicker="人物" title="先认出人，再认识他们">
          {state === "loading" ? (
            <p className="empty">正在读取人物…</p>
          ) : (
            <FamilyRoster people={people} selectedId={selectedId} onSelect={(id) => selectPerson(id)} />
          )}

        </Section>
      ) : null}

      {view === "graph" ? (
        <Section kicker="人物关系" title="辈分从上到下，还没归类的留在图上">
          {state === "loading" ? (
            <p className="empty">正在读取关系…</p>
          ) : (
            <FamilyGraphView
              people={people}
              relationships={relationships}
              selectedId={selectedId}
              onSelect={(id) => selectPerson(id)}
              onRelate={(subjectId, objectId) => selectPerson(subjectId, objectId)}
              onChanged={refresh}
            />
          )}
        </Section>
      ) : null}

      {view === "review" ? (
        <Section kicker="待确认的人物" title="先确认是谁，再进入长期记忆">
          <PeopleView
            scopeId={scopeId}
            onConfirmSubmit={(clusterId) =>
              void reportClientEvent({
                eventName: "people.confirm_submit",
                result: "started",
                operationId: newOperationId(),
                scopeId,
                entityId: clusterId,
              })
            }
            onConfirmResult={(clusterId, ok) =>
              void reportClientEvent({
                eventName: "people.confirm_result",
                result: ok ? "ok" : "failed",
                operationId: newOperationId(),
                scopeId,
                entityId: clusterId,
                errorCode: ok ? undefined : "unknown",
              })
            }
          />
        </Section>
      ) : null}

      {view === "insights" ? (
        <Section kicker="人物介绍" title="看看系统记得的这个人，像不像他">
          <PersonInsights scopeId={scopeId} />
        </Section>
      ) : null}

      {selected === null ? null : (
        <PersonDetail
          key={selected.id}
          person={selected}
          people={people}
          relationships={relationships}
          relateTarget={relateTo}
          onClose={() => selectPerson(null)}
          onChanged={refresh}
        />
      )}

      {/* 详情侧栏打开时 `selected` 一定存在；这里只兜一个读屏提示，避免侧栏静默出现。 */}
      <span className="sr-only" aria-live="polite">
        {selected === null ? "" : `已打开 ${personDisplayName(selected)} 的详情`}
      </span>
    </>
  );
}
