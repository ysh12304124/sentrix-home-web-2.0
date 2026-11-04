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
  ask: (conversationId: string, message: string, reasoning?: boolean) => Promise<void>;
}

/** 1.0 ``src/app.js:1524`` 的回退轮询间隔与上限，照抄。 */
const POLL_INTERVAL_MS = 700;
const POLL_MAX_ATTEMPTS = 150;

/**
 * 一次提问的实时过程：``POST`` 建回合，优先走 SSE，断了回退轮询。
 *
 * 主路径与回退顺序与 1.0 ``src/app.js:1482`` 一致——**先** ``EventSource``，拿不到
 * 终态才转轮询。S3 曾经把回退删掉，理由写的是「1.0 一 ``onerror`` 就永久降级成
 * 700ms 轮询，于是轮询成了主路径（668,490 次请求）」。审计推翻了这条理由：那 66 万次
 * 轮询的调用方是 ``services/photobench`` 的 ``wait_for_assistant_turn``（非浏览器，
 * 没有 ``EventSource`` 可用），不是这个 hook。浏览器侧从头到尾都是 SSE 优先的。
 *
 * ``complete`` 到达后显式 close：服务端发完那一帧就关流，浏览器会把它当成「连接断开」
 * 触发 ``onerror``；不先标记完成的话，一个正常的回合会被显示成错误。
 */
export function useTurnStream(): TurnStream {
  const [progress, setProgress] = useState<AssistantProgressEvent[]>([]);
  const [result, setResult] = useState<AssistantCompleteEvent | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const finishedRef = useRef(false);

  const close = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
  }, []);

  useEffect(() => close, [close]);

  const ask = useCallback(
    async (conversationId: string, message: string, reasoning = false) => {
      close();
      finishedRef.current = false;
      setProgress([]);
      setResult(null);
      setError(null);
      setBusy(true);

      let turnId: string;
      try {
        const started = await startTurn(conversationId, message, reasoning);
        turnId = started.turn_id;
      } catch (cause) {
        setError((cause as Error).message);
        setBusy(false);
        return;
      }

      const source = new EventSource(turnEventsUrl(turnId));
      sourceRef.current = source;

      source.addEventListener("progress", (event) => {
        const payload = JSON.parse((event as MessageEvent<string>).data) as AssistantProgressEvent;
        setProgress((previous) => [...previous, payload]);
      });

      source.addEventListener("complete", (event) => {
        const payload = JSON.parse((event as MessageEvent<string>).data) as AssistantCompleteEvent;
        finishedRef.current = true;
        setResult(payload);
        setBusy(false);
        close();
      });

      // 1.0 的回退：``es.onerror`` 交给轮询兜底。SSE 断了不等于回合失败——回合在后端
      // 照常跑完并落库，轮询照样能把它取回来。
      source.onerror = () => {
        if (finishedRef.current) {
          return;
        }
        close();
        void fallbackToPolling(turnId);
      };

      async function fallbackToPolling(id: string): Promise<void> {
        for (let attempt = 0; attempt < POLL_MAX_ATTEMPTS; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
          try {
            const polled = await pollTurn(id);
            setProgress(polled.public_progress);
            if (polled.result) {
              finishedRef.current = true;
              setResult(polled.result);
              setBusy(false);
              return;
            }
            if (polled.status === "cancelled") {
              finishedRef.current = true;
              setBusy(false);
              return;
            }
          } catch {
            // 单次轮询失败继续等——1.0 的 ``pollTurnEvents`` 也是这么做的。
          }
        }
        setError("实时连接中断了，也没能取回这次回答，请重新发送这条消息。");
        setBusy(false);
      }
    },
    [close],
  );

  return { progress, result, busy, error, ask };
}
