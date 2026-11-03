/**
 * 事件时间线。对应 1.0 `src/app.js:626` 的 `timelineView()`（pageHeader 在 `:645`）。
 *
 * 右侧 `side-inspector` 是 1.0 的「视频记忆」说明块。2.0 没有视频链路（`videos` /
 * `video-scenes` 端点都不存在），所以**这里是空状态而不是照抄那段文案**——照抄会让
 * 人以为场景图片堆是可用的。
 */

import { useSearchParams } from "react-router";

import { useScope } from "../../app/ScopeProvider";
import { PageHeader } from "../../shared/ui/PageHeader";
import { Timeline } from "./Timeline";
import "../../styles/timeline.css";

export function TimelinePage() {
  const { scopeId } = useScope();
  const [searchParams, setSearchParams] = useSearchParams();
  const focusedEventId = searchParams.get("event");

  /**
   * `?event=` 是详情的唯一状态：概览页跳过来、刷新、浏览器后退都能还原。
   *
   * **以 `window.location.search` 为准重建参数**——与 PeoplePage 的 `?person=` 同一条
   * 理由：ScopeProvider 切相册时用 `history.replaceState` 直接写 URL，react-router
   * 收不到那次变更，在 router 的旧参数上增删会把刚切的相册覆盖掉。
   */
  function openEvent(eventId: string | null) {
    const next = new URLSearchParams(window.location.search);
    if (eventId === null) next.delete("event");
    else next.set("event", eventId);
    setSearchParams(next);
  }

  return (
    <>
      <PageHeader
        kicker="家庭记忆"
        title="时间线"
        description="先选年份和月份，再展开某一天查看当天的回忆。"
      />
      <section className="timeline-layout">
        <div className="timeline-main">
          <Timeline
            scopeId={scopeId}
            focusEventId={focusedEventId}
            onOpenEvent={openEvent}
          />
        </div>
      </section>
    </>
  );
}
