/**
 * 事件时间线。对应 1.0 `src/app.js:626` 的 `timelineView()`（pageHeader 在 `:645`）。
 *
 * 右侧提示视频场景与语音转写的查看入口；视频详情在资料库资产面板中打开。
 */

import { useSearchParams } from "react-router";

import { useScope } from "../../app/ScopeProvider";
import { PageHeader } from "../../shared/ui/PageHeader";
import { Timeline } from "./Timeline";
import "../../styles/timeline.css";

export function TimelinePage() {
  const { scopeId, spaces } = useScope();
  const scopeName = spaces.find((space) => space.id === scopeId)?.name ?? "全部相册";
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
        kicker="记忆组织 / 事件"
        title="家里的时间线，不只是文件列表。"
        description={`当前范围：${scopeName}。照片和视频片段按真实拍摄时间统一整理。`}
      />
      <section className="timeline-layout">
        <div className="timeline-main">
          <Timeline
            scopeId={scopeId}
            focusEventId={focusedEventId}
            onOpenEvent={openEvent}
          />
        </div>
        <aside className="side-inspector">
          <p className="section-kicker">视频记忆</p>
          <h2>一段视频，也能成为清晰的家庭回忆</h2>
          <p>视频场景会对齐关键帧与语音片段。打开资料库中的视频详情，可查看带时间戳的转写并跳转播放。</p>
          <div className="inspector-note">
            语音对齐 <strong>已接入视频场景</strong>
            <small>转写片段保留起止时间；QA 检索可把语音作为视频证据，与画面证据一起回答问题。</small>
          </div>
        </aside>
      </section>
    </>
  );
}
