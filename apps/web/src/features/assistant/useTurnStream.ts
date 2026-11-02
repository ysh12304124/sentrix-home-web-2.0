import { useCallback, useEffect, useRef, useState } from "react";

import {
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
  ask: (conversationId: string, message: string) => Promise<void>;
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
  const sourceRef = useRef<EventSource | null>(null);
  const timerRef = useRef<number | null>(null);
  const finishedRef = useRef(false);
  const cursorRef = useRef(0);
  const seenRef = useRef<Set<number>>(new Set());

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
      cursorRef.current = 0;
      seenRef.current = new Set();
      setProgress([]);
      setResult(null);
      setError(null);
      setExecutionState("");
      setAttemptNo(0);
      setBusy(true);

      // 一次提问一枚键：网络重试命中同一个回合，不重复保存用户消息。
      const idempotencyKey =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`;

      let turnId: string;
      try {
        const started = await startTurn(conversationId, message, idempotencyKey);
        turnId = started.turn_id;
      } catch (cause) {
        setError((cause as Error).message);
        setBusy(false);
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
      };

      let attempt = 0;
      const connect = () => {
        if (finishedRef.current) return;
        let source: EventSource;
        try {
          source = new EventSource(turnEventsUrl(turnId, cursorRef.current));
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
            const polled = await pollTurn(turnId);
            polled.public_progress.forEach(pushProgress);
            if (polled.execution_state) setExecutionState(polled.execution_state);
            if (polled.attempt_no) setAttemptNo(polled.attempt_no);
            if (polled.result) {
              finish(polled.result);
              return;
            }
            if (polled.status === "cancelled" || polled.status === "failed") {
              finishedRef.current = true;
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

  return { progress, result, busy, error, executionState, attemptNo, ask };
}
