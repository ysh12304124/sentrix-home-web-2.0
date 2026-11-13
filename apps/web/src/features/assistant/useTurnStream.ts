import { useCallback, useEffect, useRef, useState } from "react";

import { newClientId } from "../../shared/id";
import { errorCodeFromStatus, reportClientEvent } from "../../shared/clientEvents";
import {
  ApiError,
  cancelTurn as cancelTurnRequest,
  pollTurn,
  startTurn,
  turnEventsUrl,
  type AssistantCompleteEvent,
  type AssistantProgressEvent,
} from "../../shared/api/client";

export interface TurnStream {
  progress: AssistantProgressEvent[];
  result: AssistantCompleteEvent | null;
  busy: boolean;
  error: string | null;
  /** 服务端执行子状态：recovering / waiting_dependency / retry_scheduled。 */
  executionState: string;
  attemptNo: number;
  /** 本轮回合 ID；还没拿到或已清空时为 null。 */
  turnId: string | null;
  /** 用户已请求停止；服务端会在安全检查点收尾。 */
  cancelled: boolean;
  ask: (conversationId: string, message: string) => Promise<void>;
  cancel: () => Promise<void>;
}

const RECONNECT_BASE_MS = 800;
const RECONNECT_MAX_MS = 10_000;
const POLL_INTERVAL_MS = 1_000;

/**
 * 一次提问的过程流。断线只重连同一个回合并从最后序号续传，绝不产生新尝试。
 *
 * 旧实现断线后只轮询约 105 秒，然后提示用户重新发送——那会把一次提问变成两次。
 * 现在只要回合没到终态就一直重连；用户问题、回合 ID 与幂等键都不变。
 */
export function useTurnStream(): TurnStream {
  const [progress, setProgress] = useState<AssistantProgressEvent[]>([]);
  const [result, setResult] = useState<AssistantCompleteEvent | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [executionState, setExecutionState] = useState("");
  const [attemptNo, setAttemptNo] = useState(0);
  const [turnId, setTurnId] = useState<string | null>(null);
  const [cancelled, setCancelled] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);
  const timerRef = useRef<number | null>(null);
  const finishedRef = useRef(false);
  const cursorRef = useRef(0);
  const seenRef = useRef<Set<number>>(new Set());
  // 回合 ID 还没返回时用户就点了停止：等 ID 到手后立刻补发取消，而不是静默吞掉。
  const cancelRequestedRef = useRef(false);
  const operationIdRef = useRef("");

  const close = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => close, [close]);

  const ask = useCallback(
    async (conversationId: string, message: string) => {
      close();
      finishedRef.current = false;
      cancelRequestedRef.current = false;
      cursorRef.current = 0;
      seenRef.current = new Set();
      setProgress([]);
      setResult(null);
      setError(null);
      setExecutionState("");
      setAttemptNo(0);
      setTurnId(null);
      setCancelled(false);
      setBusy(true);

      // 一次提问一枚键：网络重试命中同一个回合，不重复保存用户消息。
      const idempotencyKey = newClientId("turn");
      operationIdRef.current = idempotencyKey;
      void reportClientEvent({
        eventName: "assistant.ask",
        result: "started",
        operationId: idempotencyKey,
      });

      let activeTurnId: string;
      try {
        const started = await startTurn(conversationId, message, idempotencyKey);
        activeTurnId = started.turn_id;
        setTurnId(activeTurnId);
        void reportClientEvent({
          eventName: "assistant.accepted",
          result: "accepted",
          operationId: idempotencyKey,
          turnId: activeTurnId,
        });
      } catch (cause) {
        setError((cause as Error).message);
        setBusy(false);
        void reportClientEvent({
          eventName: "assistant.ask",
          result: "failed",
          operationId: idempotencyKey,
          errorCode: cause instanceof ApiError ? errorCodeFromStatus(cause.status) : "network",
        });
        return;
      }

      if (cancelRequestedRef.current) {
        cancelRequestedRef.current = false;
        try {
          await cancelTurnRequest(activeTurnId);
          finishedRef.current = true;
          close();
          setCancelled(true);
          setBusy(false);
          setExecutionState("");
        } catch (cause) {
          setError((cause as Error).message);
        }
        return;
      }

      const pushProgress = (payload: AssistantProgressEvent) => {
        if (seenRef.current.has(payload.step_index)) return;
        seenRef.current.add(payload.step_index);
        cursorRef.current = Math.max(cursorRef.current, payload.step_index);
        setProgress((previous) => [...previous, payload]);
      };

      const finish = (payload: AssistantCompleteEvent) => {
        finishedRef.current = true;
        setResult(payload);
        setBusy(false);
        close();
        const result =
          payload.status === "partial"
            ? "partial"
            : payload.status === "failed" || payload.status === "blocked"
              ? "failed"
              : payload.status === "cancelled"
                ? "cancelled"
                : "ok";
        void reportClientEvent({
          eventName: "assistant.complete",
          result,
          operationId: operationIdRef.current,
          turnId: payload.turn_id || activeTurnId,
          errorCode: result === "failed" ? "unknown" : undefined,
        });
      };

      let attempt = 0;
      const connect = () => {
        if (finishedRef.current) return;
        let source: EventSource;
        try {
          source = new EventSource(turnEventsUrl(activeTurnId, cursorRef.current));
        } catch {
          void pollUntilTerminal();
          return;
        }
        sourceRef.current = source;

        source.addEventListener("progress", (event) => {
          try {
            pushProgress(JSON.parse((event as MessageEvent<string>).data) as AssistantProgressEvent);
          } catch {
            // 坏帧跳过，不影响续传。
          }
        });
        source.addEventListener("status", (event) => {
          try {
            const payload = JSON.parse((event as MessageEvent<string>).data) as {
              execution_state?: string;
              attempt_no?: number;
            };
            if (payload.execution_state) setExecutionState(payload.execution_state);
            if (typeof payload.attempt_no === "number") setAttemptNo(payload.attempt_no);
          } catch {
            // 状态帧坏了就保持上一状态。
          }
        });
        source.addEventListener("complete", (event) => {
          try {
            finish(JSON.parse((event as MessageEvent<string>).data) as AssistantCompleteEvent);
          } catch {
            finishedRef.current = true;
            setError("回答已结束，但终态数据无法解析。");
            setBusy(false);
            close();
          }
        });
        source.onerror = () => {
          if (finishedRef.current) return;
          source.close();
          attempt += 1;
          const delay = Math.min(RECONNECT_BASE_MS * 2 ** Math.min(attempt, 4), RECONNECT_MAX_MS);
          timerRef.current = window.setTimeout(connect, delay);
        };
      };

      async function pollUntilTerminal(): Promise<void> {
        while (!finishedRef.current) {
          await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS));
          try {
            const polled = await pollTurn(activeTurnId);
            polled.public_progress.forEach(pushProgress);
            if (polled.execution_state) setExecutionState(polled.execution_state);
            if (polled.attempt_no) setAttemptNo(polled.attempt_no);
            if (polled.result) {
              finish(polled.result);
              return;
            }
            if (polled.status === "cancelled" || polled.status === "failed") {
              finishedRef.current = true;
              if (polled.status === "cancelled") setCancelled(true);
              else setError("这次回答没有完成，可以重新提问。");
              setBusy(false);
              return;
            }
          } catch {
            // 继续等：控制面或用户 API 可能正在重启。
          }
        }
      }

      connect();
    },
    [close],
  );

  const cancel = useCallback(async () => {
    if (finishedRef.current) return;
    if (turnId === null) {
      // 回合还在创建：先记下意图，startTurn 返回后立即取消。
      if (busy) {
        cancelRequestedRef.current = true;
        void reportClientEvent({
          eventName: "assistant.stop",
          result: "cancelled",
          operationId: operationIdRef.current,
        });
      }
      return;
    }
    try {
      await cancelTurnRequest(turnId);
      void reportClientEvent({
        eventName: "assistant.stop",
        result: "cancelled",
        operationId: operationIdRef.current,
        turnId,
      });
      // 停止接收后续帧：服务端会在安全检查点收尾，界面不再等一个不会来的 complete。
      finishedRef.current = true;
      close();
      setCancelled(true);
      setBusy(false);
      setExecutionState("");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, [busy, close, turnId]);

  return {
    progress,
    result,
    busy,
    error,
    executionState,
    attemptNo,
    turnId,
    cancelled,
    ask,
    cancel,
  };
}
