/**
 * 关键操作轨迹上报。
 *
 * 只上报固定事件名、路由模板、结果码和已有对象 ID；不发送文件名、输入正文、
 * 查询串、媒体地址或异常文本。失败静默且做节流，不能影响上传、问答等主流程。
 */

import { csrfToken } from "./csrf";
import { newClientId } from "./id";

export type ClientEventName =
  | "page.view"
  | "nav.click"
  | "scope.change"
  | "feedback.open"
  | "auth.logout"
  | "onboarding.open"
  | "onboarding.skip"
  | "onboarding.complete"
  | "help.open"
  | "memory.next"
  | "memory.prev"
  | "memory.pause"
  | "memory.resume"
  | "memory.video_play"
  | "memory.media_error"
  | "import.pick"
  | "import.upload_start"
  | "import.upload_accepted"
  | "import.upload_failed"
  | "import.cancel"
  | "assistant.ask"
  | "assistant.accepted"
  | "assistant.stop"
  | "assistant.complete"
  | "people.view"
  | "people.detail_open"
  | "people.confirm_submit"
  | "people.confirm_result"
  | "people.filter"
  | "library.view"
  | "library.detail_open"
  | "library.filter"
  | "library.media_error"
  | "timeline.month"
  | "timeline.date"
  | "timeline.event_open"
  | "timeline.media_error";

export type ClientEventResult =
  | "started"
  | "accepted"
  | "ok"
  | "partial"
  | "failed"
  | "cancelled"
  | "rejected"
  | "ignored";

export type ClientEventError =
  | "network"
  | "timeout"
  | "http_4xx"
  | "http_5xx"
  | "media_load"
  | "video_play"
  | "unknown";

type RouteTemplate = "/" | "/assistant" | "/timeline" | "/people" | "/library" | "/imports";

const ROUTES: readonly RouteTemplate[] = [
  "/",
  "/assistant",
  "/timeline",
  "/people",
  "/library",
  "/imports",
];

export interface ClientEventInput {
  eventName: ClientEventName;
  result: ClientEventResult;
  operationId?: string;
  errorCode?: ClientEventError;
  scopeId?: string;
  jobId?: string;
  turnId?: string;
  eventId?: string;
  assetId?: string;
  entityId?: string;
}

/** 当前页面对应的固定路由模板；未知路径不猜，落到回忆。 */
export function currentRouteTemplate(): RouteTemplate {
  if (typeof window === "undefined") return "/";
  const path = window.location.pathname;
  const exact = ROUTES.find((route) => route === path);
  if (exact !== undefined) return exact;
  return (
    ROUTES.find((route) => route !== "/" && path.startsWith(`${route}/`)) ?? "/"
  );
}

const FAILURE_THROTTLE_MS = 10_000;
const lastFailureAt = new Map<string, number>();

export function newOperationId(): string {
  return newClientId("op");
}

/** 即发即忘。返回的 Promise 永远 resolve，调用方不必 await。 */
export function reportClientEvent(input: ClientEventInput): Promise<void> {
  const operationId = input.operationId ?? newOperationId();
  const throttled = `${input.eventName}:${input.errorCode ?? ""}`;
  if (input.result === "failed" || input.result === "rejected") {
    const now = Date.now();
    const previous = lastFailureAt.get(throttled) ?? 0;
    if (now - previous < FAILURE_THROTTLE_MS) return Promise.resolve();
    lastFailureAt.set(throttled, now);
  }
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return Promise.resolve();
  }
  return fetch("/control/v1/client-events", {
    method: "POST",
    credentials: "same-origin",
    keepalive: true,
    headers: { "Content-Type": "application/json", "x-csrf-token": csrfToken() },
    body: JSON.stringify({
      event_name: input.eventName,
      route: currentRouteTemplate(),
      operation_id: operationId,
      result: input.result,
      error_code: input.errorCode ?? "",
      scope_id: input.scopeId ?? "",
      job_id: input.jobId ?? "",
      turn_id: input.turnId ?? "",
      event_id: input.eventId ?? "",
      asset_id: input.assetId ?? "",
      entity_id: input.entityId ?? "",
    }),
  })
    .then(() => undefined)
    .catch(() => undefined);
}

/** 把 HTTP 错误归到一个有限错误码，避免把自由文本送进控制库。 */
export function errorCodeFromStatus(status: number): ClientEventError {
  if (status >= 500) return "http_5xx";
  if (status >= 400) return "http_4xx";
  return "unknown";
}
